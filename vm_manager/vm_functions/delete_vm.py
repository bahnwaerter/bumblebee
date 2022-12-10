from datetime import datetime, timedelta
import logging

import django_rq

from django.conf import settings
from django.utils.timezone import utc

from vm_manager.cloud.connector.connector import get_cloud_connector
from vm_manager.cloud.connector.exception import CloudConnectorError
from vm_manager.cloud.connector.objects import Server, ServerStatus, Volume, VolumeStatus, VolumeBackup
from vm_manager.cloud.environment.environment import get_cloud_environment
from vm_manager.constants import NO_VM, VM_SHELVED, VM_WAITING, \
    INSTANCE_DELETION_RETRY_WAIT_TIME, INSTANCE_DELETION_RETRY_COUNT, \
    VOLUME_DELETION_RETRY_WAIT_TIME, VOLUME_DELETION_RETRY_COUNT, \
    BACKUP_DELETION_RETRY_WAIT_TIME, BACKUP_DELETION_RETRY_COUNT, \
    INSTANCE_CHECK_SHUTOFF_RETRY_WAIT_TIME, \
    INSTANCE_CHECK_SHUTOFF_RETRY_COUNT, \
    ARCHIVE_POLL_SECONDS, ARCHIVE_WAIT_SECONDS, \
    WF_RETRY, WF_SUCCESS, WF_FAIL, WF_CONTINUE
from vm_manager.models import VMStatus, Expiration, \
    EXP_EXPIRING, EXP_EXPIRY_COMPLETED, \
    EXP_EXPIRY_FAILED, EXP_EXPIRY_FAILED_RETRYABLE

from guacamole.models import GuacamoleConnection


logger = logging.getLogger(__name__)

# Combine the delete and archive workflows into one module because they
# are too difficult to separate.  (I tried a dynamic import, but it made
# it too hard to implement proper unit tests.)


def delete_vm_worker(instance, archive=False):
    logger.info(f"About to delete {instance}")

    if instance.guac_connection:
        GuacamoleConnection.objects.filter(instance=instance).delete()
        instance.guac_connection = None
        instance.save()

    cloud = get_cloud_connector()
    server = Server.create(instance)
    server_exist = cloud.is_server_created(server)
    if server_exist is None or server_exist is False:
        logger.error(f"Trying to delete {instance} but it is not "
                     f"found in Nova.")
        # It no longer matters, but record the fact that the Nova instance
        # went missing anyway.
        instance.error(f"Nova instance is missing", gone=True)
    else:
        status = cloud.get_server_status(server)
        if status == ServerStatus.ACTIVE:
            cloud.stop_server(server)
        elif status == ServerStatus.SHUTOFF:
            logger.info(f"{instance} already shutdown in Nova.")
        else:
            # Possible states include stuck while resizing, paused / locked
            # due to security incident, ERROR cause by stuck launching (?)
            logger.error(f"Cloud instance for {instance} is in unexpected "
                         f"state {status}. Needs manual cleanup.")
            instance.error(f"Nova instance state is {status}")
            return WF_RETRY

    # Next step is to check if the Instance is Shutoff in Nova before
    # telling Nova to delete it
    scheduler = django_rq.get_scheduler('default')
    scheduler.enqueue_in(
        timedelta(seconds=INSTANCE_CHECK_SHUTOFF_RETRY_WAIT_TIME),
        _check_instance_is_shutoff_and_delete, instance,
        INSTANCE_CHECK_SHUTOFF_RETRY_COUNT,
        _dispose_volume_once_instance_is_deleted,
        (instance, archive, INSTANCE_DELETION_RETRY_COUNT))
    return WF_CONTINUE


def _check_instance_is_shutoff_and_delete(
        instance, retries, func, func_args):
    logger.info(f"Checking whether {instance} is ShutOff.")
    scheduler = django_rq.get_scheduler('default')
    if not instance.check_shutdown_status() and retries > 0:
        # If the instance is not Shutoff, schedule the recheck
        logger.info(f"{instance} is not yet SHUTOFF! Will check again "
                    f"in {INSTANCE_CHECK_SHUTOFF_RETRY_WAIT_TIME} seconds with "
                    f"{retries} retries remaining.")
        scheduler.enqueue_in(
            timedelta(seconds=INSTANCE_CHECK_SHUTOFF_RETRY_WAIT_TIME),
            _check_instance_is_shutoff_and_delete, instance,
            retries - 1, func, func_args)
        return WF_CONTINUE
    if retries <= 0:
        # TODO - not sure we should delete the instance anyway ...
        logger.info(f"Ran out of retries shutting down {instance}. "
                    f"Proceeding to delete Nova instance anyway!")

    # Update status if something is waiting
    vm_status = VMStatus.objects.get_vm_status_by_instance(
        instance, instance.boot_volume.requesting_feature, allow_missing=True)
    if vm_status and vm_status.status == VM_WAITING:
        vm_status.status_progress = 66
        vm_status.status_message = 'Instance shelving'
        vm_status.save()

    if not delete_instance(instance):
        return WF_FAIL

    # The 'func' will do the next step; e.g. delete the volume
    # or mark the volume as shelved.
    scheduler.enqueue_in(
        timedelta(seconds=INSTANCE_DELETION_RETRY_WAIT_TIME),
        func, *func_args)
    return WF_CONTINUE


def delete_instance(instance):
    cloud = get_cloud_connector()
    server = Server(id=instance.id)
    deleted = cloud.delete_server(server)
    logger.info(f"Instructed cloud to delete {instance}")
    if deleted is None or deleted is False:
        return False
    else:
        instance.marked_for_deletion = datetime.now(utc)
        instance.save()
        return True


def _dispose_volume_once_instance_is_deleted(instance, archive, retries):
    cloud = get_cloud_connector()
    server = Server(id=instance.id)
    server_exist = cloud.is_server_created(server)
    if server_exist is None:
        logger.exception(f"Instance get call for {instance} failed")
        return WF_RETRY
    elif server_exist:
        logger.debug(f"Instance delete status is retries: {retries} "
                     f"Cloud instance: {server.id}")
    else:
        instance.deleted = datetime.now(utc)
        instance.save()
        volume = instance.boot_volume
        if archive:
            logger.info(f"Instance {instance.id} successfully deleted. "
                        f"Proceeding to archive {volume} now!")
            return archive_volume_worker(
                volume, volume.requesting_feature)
        else:
            logger.info(f"Instance {instance.id} successfully deleted. "
                        f"Proceeding to delete {volume} now!")
            if delete_volume(volume):
                scheduler = django_rq.get_scheduler('default')
                scheduler.enqueue_in(
                    timedelta(seconds=VOLUME_DELETION_RETRY_WAIT_TIME),
                    _wait_until_volume_is_deleted, volume,
                    VOLUME_DELETION_RETRY_COUNT)
                return WF_CONTINUE
            else:
                return _end_delete(volume, WF_RETRY)

    # Nova still has the instance
    if retries > 0:
        scheduler = django_rq.get_scheduler('default')
        # Note in this case I'm using `minutes=` not `seconds=` to give
        # a long wait time that should be sufficient
        scheduler.enqueue_in(
            timedelta(minutes=INSTANCE_DELETION_RETRY_WAIT_TIME),
            _dispose_volume_once_instance_is_deleted, instance, archive,
            retries - 1)
        return WF_CONTINUE
    else:
        error_message = f"Ran out of retries trying to delete"
        instance.error(error_message)
        logger.error(f"{error_message} {instance}")
        return WF_RETRY


def delete_volume_worker(volume):
    if delete_volume(volume):
        return _wait_until_volume_is_deleted(
            volume, VOLUME_DELETION_RETRY_COUNT)
    else:
        return WF_FAIL


def delete_volume(volume):
    cloud = get_cloud_connector()
    cloud_volume = Volume(volume.id)
    deleted = cloud.delete_volume(cloud_volume)
    logger.info(f"Instructed cloud to delete {volume}")
    if deleted is None or deleted is False:
        return False
    else:
        volume.deleted = datetime.now(utc)
        volume.save()
        return True


def _wait_until_volume_is_deleted(volume, retries):
    cloud = get_cloud_connector()
    cloud_volume = Volume(id=volume.id)
    volume_exist = cloud.is_volume_created(cloud_volume)
    if volume_exist is None:
        logger.exception(f"Volume get call for {volume} failed")
        return _end_delete(volume, WF_RETRY)
    elif volume_exist is False:
        logger.info(f"Cinder volume deletion completed for {volume}")
        volume.deleted = datetime.now(utc)
        volume.save()
        return _end_delete(volume, WF_SUCCESS)

    volume_status = cloud.get_volume_status(volume)
    if volume_status != VolumeStatus.DELETING:
        logger.error(f"Cloud volume delete failed for {volume}: "
                     f"status is {volume_status}")
        return _end_delete(volume, WF_RETRY)

    if retries > 0:
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(
            timedelta(seconds=VOLUME_DELETION_RETRY_WAIT_TIME),
            _wait_until_volume_is_deleted, volume, retries - 1)
        return WF_CONTINUE
    else:
        error_message = f"Ran out of retries trying to delete"
        volume.error(error_message)
        logger.error(f"{error_message} {volume}")
        return _end_delete(volume, WF_RETRY)


def delete_backup_worker(volume):
    if not volume.backup_id:
        logger.info(f"No backup to delete for {volume}")
        return WF_SUCCESS
    cloud = get_cloud_connector()
    deleted = cloud.delete_volume_backup(volume.backup_id)
    logger.info(f"Cloud backup delete requested for {volume}, "
                f"backup {volume.backup_id}")
    if deleted is None:
        logger.exception(f"Cinder backup delete failed for {volume}, "
                         f"backup {volume.backup_id}")
        return WF_RETRY
    elif deleted:
        logger.info(f"Cinder backup already deleted for {volume}, "
                    f"backup {volume.backup_id}")
        volume.backup_id = None
        volume.save()
        return WF_SUCCESS

    scheduler = django_rq.get_scheduler('default')
    scheduler.enqueue_in(
        timedelta(seconds=BACKUP_DELETION_RETRY_WAIT_TIME),
        _wait_until_backup_is_deleted, volume,
        BACKUP_DELETION_RETRY_COUNT)
    return WF_CONTINUE


def _wait_until_backup_is_deleted(volume, retries):
    cloud = get_cloud_connector()
    cloud_volume_backup = VolumeBackup(id=volume.backup_id)
    backup_exist = cloud.is_backup_created(cloud_volume_backup)
    if backup_exist is None:
        logger.exception(f"Cinder backup get failed for {volume}, "
                         f"backup {volume.backup_id}")
        return _end_delete(volume, WF_RETRY)
    elif backup_exist is False:
        logger.info(f"Cinder backup for {volume} has been deleted, "
                    f"backup {volume.backup_id}")
        volume.backup_id = None
        volume.save()
        return _end_delete(volume, WF_SUCCESS)

    if retries > 0:
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(
            timedelta(seconds=BACKUP_DELETION_RETRY_WAIT_TIME),
            _wait_until_backup_is_deleted, volume,
            retries - 1)
        return WF_CONTINUE
    else:
        logger.info("Cinder backup deletion took too long for {volume}, "
                    f"backup {volume.backup_id}")
        return _end_delete(volume, WF_RETRY)


def _end_delete(volume, wf_status):
    for expiration in [volume.expiration, volume.backup_expiration]:
        if not expiration:
            continue
        expiration = Expiration.objects.get(pk=expiration.pk)
        if expiration.stage == EXP_EXPIRING:
            if wf_status == WF_FAIL:
                expiration.stage = EXP_EXPIRY_FAILED
            elif wf_status == WF_RETRY:
                expiration.stage = EXP_EXPIRY_FAILED_RETRYABLE
            elif wf_status == WF_SUCCESS:
                expiration.stage = EXP_EXPIRY_COMPLETED
            expiration.stage_date = datetime.now(utc)
            expiration.save()
    return wf_status


def archive_volume_worker(volume, requesting_feature):
    '''Archive a volume by creating a Cinder backup then deleting the Cinder
    volume.
    '''

    # This "hides" the volume from the get_volume method allowing
    # another one to be created / launched without errors.
    volume.marked_for_deletion = datetime.now(utc)
    volume.save()

    cloud = get_cloud_connector()
    cloud_volume = Volume(id=volume.id)
    volume_exist = cloud.is_volume_created(cloud_volume)
    if volume_exist is None or volume_exist is False:
        volume.error("Cloud volume missing. Cannot be archived.")
        logger.error(f"Cloud volume missing for {volume}. "
                     f"Cannot be archived.")
        return _end_delete(volume, WF_SUCCESS)
    else:
        volume_status = cloud.get_volume_status(cloud_volume)
        if volume_status != VolumeStatus.AVAILABLE:
            logger.error(
                f"Cannot archive volume with cloud volume status "
                f"{volume_status}: {volume}. Manual cleanup needed.")
            return _end_delete(volume, WF_RETRY)

    try:
        cloud_volume_backup = cloud.create_volume_backup(volume=cloud_volume, name=f"{volume.id}-archive")
        logger.info(f'Cinder backup {cloud_volume_backup.id} started for volume {volume.id}')
    except CloudConnectorError:
        volume.error("Cinder backup failed")
        logger.error(f"Cinder backup failed for volume {volume.id}")
        return _end_delete(volume, WF_RETRY)

    cenv = get_cloud_environment()
    scheduler = django_rq.get_scheduler('default')
    scheduler.enqueue_in(timedelta(seconds=5), wait_for_backup,
                         volume, cloud_volume_backup.id,
                         cenv.after_time(ARCHIVE_WAIT_SECONDS))

    # This allows the user to launch a new desktop immediately.
    vm_status = VMStatus.objects.get_vm_status_by_volume(
        volume, requesting_feature, allow_missing=True)
    if vm_status:
        vm_status.status = NO_VM
        vm_status.save()

    return WF_CONTINUE


def wait_for_backup(volume, backup_id, deadline):
    cloud = get_cloud_connector()
    cloud_backup = VolumeBackup(id=backup_id)
    backup_exist = cloud.is_backup_created(cloud_backup)
    if backup_exist is None or backup_exist is False:
        # The backup has disappeared ...
        logger.error(f"Backup {backup_id} for volume {volume} not "
                     "found. Presumed failed.")
        return _end_delete(volume, WF_RETRY)

    backup_status = cloud.get_backup_status(cloud_backup)

    if backup_status == VolumeStatus.BACKING_UP:
        if datetime.now(utc) > deadline:
            logger.error(f"Backup took too long: backup {backup_id}, "
                         f"volume {volume}")
            return _end_delete(volume, WF_RETRY)
        scheduler = django_rq.get_scheduler('default')
        scheduler.enqueue_in(timedelta(seconds=ARCHIVE_POLL_SECONDS),
                             wait_for_backup, volume, backup_id, deadline)
        return WF_CONTINUE
    elif backup_status == VolumeStatus.AVAILABLE:
        logger.info(f"Backup {backup_id} completed for volume {volume}")
        volume.backup_id = backup_id
        volume.archived_at = datetime.now(utc)
        volume.save()
        volume.set_backup_expires(
            datetime.now(utc) + timedelta(days=settings.BACKUP_LIFETIME))
        logger.info(f"About to delete the archived volume {volume}")
        delete_volume(volume)
        return _end_delete(volume, WF_SUCCESS)
    else:
        logger.error(f"Backup {backup_id} for volume {volume} is in "
                     f"unexpected state {backup_status}")
        return _end_delete(volume, WF_FAIL)


def archive_expired_volume(volume, requesting_feature):
    try:
        vm_status = VMStatus.objects.get_vm_status_by_volume(
            volume, requesting_feature)
        if vm_status.status != VM_SHELVED:
            logger.info(f"Skipping archiving of {volume} "
                        f"in unexpected state: {vm_status}")
            return WF_SKIP
        else:
            return archive_volume_worker(volume, requesting_feature)
    except Exception:
        # FIX ME - this isn't right ...
        logger.exception(f"Cannot retrieve vm_status for {volume}")
    return WF_FAIL
