import crypt
from datetime import datetime, timedelta
import logging

import cinderclient
import django_rq

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timezone import utc

from vm_manager.cloud.connector.connector import get_cloud_connector
from vm_manager.cloud.connector.objects import Volume as CloudVolume, Network, \
    ServerStatus
from vm_manager.cloud.connector.objects import VolumeStatus
from vm_manager.cloud.environment.environment import get_cloud_environment
from vm_manager.constants import NO_VM, VM_SHELVED, VOLUME_CREATION_TIMEOUT, \
    INSTANCE_LAUNCH_TIMEOUT
from vm_manager.utils.expiry import InstanceExpiryPolicy
from vm_manager.models import Instance, Volume, VMStatus

from researcher_desktop.models import AvailabilityZone

from guacamole.models import GuacamoleConnection

logger = logging.getLogger(__name__)


def launch_vm_worker(user, desktop_type, zone):
    desktop_id = desktop_type.id
    logger.info(f'Launching {desktop_id} VM for {user.username}')

    instance = Instance.objects.get_instance(user, desktop_type)
    if instance:
        vm_status = VMStatus.objects.get_vm_status_by_instance(
            instance, desktop_type.feature)
        if vm_status.status != NO_VM:
            msg = f"A {desktop_id} VM for {user} already exists"
            logger.error(msg)
            raise RuntimeWarning(msg)

    volume = _create_volume(user, desktop_type, zone)
    if volume:
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(timedelta(seconds=5), wait_to_create_instance,
                             user, desktop_type, volume,
                             datetime.now(utc))
        logger.info(f'{desktop_id} VM creation scheduled '
                    f'for {user.username}')


def _create_volume(user, desktop_type, zone):
    desktop_id = desktop_type.id
    requesting_feature = desktop_type.feature
    volume = Volume.objects.get_volume(user, desktop_type)
    if volume:
        # Check that the volume still exists in Cinder, and that it
        # has status 'available', and is in the expected AZ.
        cloud = get_cloud_connector()
        cloud_volume = CloudVolume.create(volume)
        volume_exist = cloud.is_volume_created(volume)
        if volume_exist is None or volume_exist is False:
            logger.error(f"Cinder volume missing for {volume}. "
                         "Needs manual cleanup.")
            volume.error("Cinder volume missing")
            return None
        else:
            cloud_volume_status = cloud.get_volume_status(cloud_volume)
            if cloud_volume_status != VolumeStatus.AVAILABLE:
                logger.error(f"Cinder volume for {volume} in wrong state "
                             f"{cloud_volume_status}. Needs manual cleanup.")
                volume.error(f"Cinder volume in state {cloud_volume_status}")
                return None
            cloud_volume_zone = cloud.get_volume_zone(cloud_volume)
            if cloud_volume_zone != zone.name:
                logger.error(f"Cinder volume for {volume} in wrong AZ. "
                             "Needs manual cleanup")
                volume.error("Cinder volume in wrong AZ")
                return None

        vm_status = VMStatus.objects.get_vm_status_by_volume(
            volume, requesting_feature)
        if vm_status.status == VM_SHELVED:
            if volume.archived_at:
                logger.error("Cannot launch shelved volume marked as "
                             f"archived: {volume}")
                return None

            volume.set_expires(None)
            return volume
        else:
            # It looks like a volume exists for the user, but it is not
            # shelved.   We need to either delete it, or do something to
            # recover it; i.e. make it unshelvable.
            logger.error(f"VMstatus {vm_status.status} inconsistent with "
                         f"existing {volume} in _create_volume.  Needs "
                         "manual cleanup.")
            return None

    # Either there is no existing volume, or we have decided to ignore it
    # and make a new one for the user.

    vm_status = VMStatus.objects.get_latest_vm_status(user, desktop_type)
    if vm_status:
        vm_status.status_progress = 25
        vm_status.status_message = 'Creating volume'
        vm_status.save()

    cenv = get_cloud_environment()
    name = cenv.generate_server_name(user.username, desktop_id)
    cloud_source_volume = _get_source_volume(desktop_type, zone)

    cloud_volume_metadata = {
        'hostname': cenv.generate_hostname(volume.hostname_id, desktop_id),
        'user': user.email,
        'desktop': desktop_id,
        'environment': settings.ENVIRONMENT_NAME,
        'requesting_feature': requesting_feature.name,
    }

    cloud = get_cloud_connector()
    cloud_volume = cloud.create_volume(name=name,
                                       size=desktop_type.volume_size,
                                       source_volume=cloud_source_volume,
                                       metadata=cloud_volume_metadata,
                                       zone=zone.name)

    # Create record in DB
    volume = Volume(
        id=cloud_volume.id, user=user,
        image=cloud_source_volume.id,
        requesting_feature=requesting_feature,
        operating_system=desktop_id,
        zone=zone.name,
        flavor=desktop_type.default_flavor.id)
    volume.save()

    return volume


def _get_source_volume(desktop_type, zone):
    cloud = get_cloud_connector()
    res = cloud.get_volume_list(
        search_opts={'name~': desktop_type.image_name,
                     'availability_zone': zone.name,
                     'status': VolumeStatus.AVAILABLE.name})
    # The 'name~' is supposed to be a "fuzzy match", but it doesn't work
    # as expected.  (Maybe it is a Cinder config thing?)  At any rate,
    # even if it did work, we still need to do our own filtering to
    # 1) ensure we have a prefix match, and 2) pick the latest (tested)
    # image based on the image metadata.
    candidates = res or []
    # Interim logic ...
    matches = sorted(
        [v for v in candidates if v.name.startswith(desktop_type.image_name)],
        key=lambda v: int(v.metadata.get('nectar_build', 0)), reverse=True)

    if len(matches) < 1:
        msg = (
            f"No source volume with image names starting with "
            f"{desktop_type.image_name} in availability zone {zone.name})")
        logger.error(msg)
        raise RuntimeWarning(msg)

    match = matches[0]
    logger.debug(f"Found source volume: {match.name} ({match.id}) in "
                 f"availability zone {zone.name}")
    return match


def wait_to_create_instance(user, desktop_type, volume, start_time):
    cloud = get_cloud_connector()
    now = datetime.now(utc)
    volume_status = cloud.get_volume_status(volume.id)
    logger.info(f"Volume created in {now - start_time}s; "
                f"volume status is {volume_status}")

    if volume_status == VolumeStatus.AVAILABLE:
        instance = _create_instance(user, desktop_type, volume)
        vm_status = VMStatus.objects.get_latest_vm_status(user, desktop_type)
        vm_status.instance = instance
        vm_status.status_progress = 50
        if volume.shelved_at:
            vm_status.status_message = 'Unshelving instance'
        else:
            vm_status.status_message = 'Volume created, launching instance'
        vm_status.save()

        volume.shelved_at = None
        volume.expiry = None
        volume.save()
        logger.info(f'{desktop_type.name} VM creation initiated '
                    f'for {user.username}')
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(timedelta(seconds=5), wait_for_instance_active,
                             user, desktop_type, instance,
                             datetime.now(utc))

    elif (now - start_time > timedelta(seconds=VOLUME_CREATION_TIMEOUT)):
        logger.error(f"Volume took too long to create: user:{user} "
                     f"desktop_id:{desktop_type.id} volume:{volume} "
                     f"volume.status:{volume_status} "
                     f"start_time:{start_time} "
                     f"datetime.now:{now}")
        msg = "Volume took too long to create"
        vm_status = VMStatus.objects.get_latest_vm_status(user, desktop_type)
        vm_status.status = NO_VM
        vm_status.status_message = msg
        vm_status.save()
        volume.error(msg)
        volume.save()
        raise TimeoutError(msg)

    else:
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(timedelta(seconds=5), wait_to_create_instance,
                             user, desktop_type, volume, start_time)


def _create_instance(user, desktop_type, volume):
    cloud = get_cloud_connector()
    cenv = get_cloud_environment()
    desktop_id = desktop_type.id
    hostname = cenv.generate_hostname(volume.hostname_id, desktop_id)
    name = cenv.generate_server_name(user.username, desktop_id)

    # Reuse the previous username and password
    last_instance = Instance.objects.get_latest_instance_for_volume(volume)
    if last_instance:
        username = last_instance.username
        password = last_instance.password
    else:
        username = 'vdiuser'
        password = cenv.generate_password()

    metadata_server = {
        'allow_user': user.username,
        'environment': settings.ENVIRONMENT_NAME,
        'requesting_feature': desktop_type.feature.name,
    }

    zone = volume.zone
    network_id = AvailabilityZone.objects.get(name=zone).network_id
    nics = [Network(id=network_id)]

    desktop_timezone = user.profile.timezone or settings.TIME_ZONE
    user_data_context = {
        'hostname': hostname,
        'notify_url': (settings.SITE_URL
                       + reverse('researcher_desktop:notify_vm')),
        'phone_home_url': (settings.SITE_URL
                           + reverse('researcher_desktop:phone_home')),
        'username': username,
        'password': crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512)),
        'timezone': desktop_timezone,
    }
    user_data = render_to_string('vm_manager/cloud-config',
                                 user_data_context)

    # Create instance in OpenStack
    cloud_volume = CloudVolume.create(volume)
    server = cloud.create_server(
        name=name,
        flavor=desktop_type.default_flavor.id,
        volume=cloud_volume,
        userdata=user_data,
        security_groups=desktop_type.security_groups,
        networks=nics,
        zone=zone,
        metadata=metadata_server,
        key_name=settings.OS_KEYNAME)

    # Create guac connection
    full_name = user.get_full_name()
    desktop_name = desktop_type.name
    connection_name = f"{full_name}'s {desktop_name} desktop"
    guac_connection = GuacamoleConnection.objects.create(
        connection_name=connection_name)

    # Create record in DB
    instance = Instance.objects.create(
        id=server.id, user=user, boot_volume=volume,
        guac_connection=guac_connection,
        username=username,
        password=password)

    logger.info(f"Completed creating {instance}")

    return instance


def wait_for_instance_active(user, desktop_type, instance, start_time):
    now = datetime.now(utc)
    if instance.check_active_status():
        logger.info(f"Instance {instance.id} is now {ServerStatus.ACTIVE.name}")
        vm_status = VMStatus.objects.get_vm_status_by_instance(
            instance, desktop_type.feature)
        vm_status.status_progress = 75
        vm_status.status_message = 'Instance launched; waiting for boot'
        vm_status.save()
        instance.set_expires(
            InstanceExpiryPolicy().initial_expiry(now=instance.created))
    elif (now - start_time > timedelta(seconds=INSTANCE_LAUNCH_TIMEOUT)):
        logger.error(f"Instance took too long to launch: user:{user} "
                     f"desktop:{desktop_type.id} instance:{instance} "
                     f"instance.status:{instance.get_status()} "
                     f"start_time:{start_time} "
                     f"datetime.now:{now}")
        msg = "Instance took too long to launch"
        vm_status = VMStatus.objects.get_latest_vm_status(user, desktop_type)
        vm_status.status = NO_VM
        vm_status.status_message = msg
        vm_status.save()
        instance.error(msg)
    else:
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(timedelta(seconds=5), wait_for_instance_active,
                             user, desktop_type, instance, start_time)


# TODO - Analyse for possible race conditions with create/delete
def extend_instance(user, vm_id, requesting_feature) -> str:
    instance = Instance.objects.get_instance_by_untrusted_vm_id(
        vm_id, user, requesting_feature)
    logger.info(f"Extending the expiration of boosted "
                f"{instance.boot_volume.operating_system} vm "
                f"for user {user.username}")
    instance.set_expires(InstanceExpiryPolicy().new_expiry(instance))
    return str(instance)
