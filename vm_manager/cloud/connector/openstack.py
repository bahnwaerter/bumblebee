from abc import abstractmethod
import logging
from enum import Enum
from typing import Dict, Optional, List, Tuple
from urllib import parse
from uuid import UUID

from cinderclient import client as cinder_client
from cinderclient import exceptions as cinder_exceptions
from cinderclient.v3.volumes import Volume as CinderVolume
from cinderclient.v3.volume_backups import VolumeBackup as CinderBackup
from django.conf import settings, LazySettings
from glanceclient import client as glance_client
from keystoneauth1.access import AccessInfo
from keystoneauth1.identity.v3 import ApplicationCredential
from keystoneauth1.session import Session
from keystoneclient import client as keystone_client
from nectarallocationclient import client as allocation_client
from novaclient import client as nova_client
from novaclient import exceptions as nova_exceptions
from novaclient.v2.servers import Server as NovaServer

from vm_manager.cloud.connector.connector import CloudConnector
from vm_manager.cloud.connector.exception import CloudConnectorError
from vm_manager.cloud.connector.objects import Server, Volume, ConsoleInfo, Network, ConsoleProtocol, VolumeStatus, \
    ServerStatus, Flavor, StatusMapper, VolumeBackup

logger = logging.getLogger(__name__)


class OpenStackServerStatus(Enum):
    # default status
    UNKNOWN = "UNKNOWN"

    # fault status
    ERROR = "ERROR"
    DELETED = "DELETED"

    # progress status
    ACTIVE = "ACTIVE"
    BUILD = "BUILD"
    REBUILD = "REBUILD"
    RESIZE = "RESIZE"
    VERIFY_RESIZE = "VERIFY_RESIZE"
    MIGRATING = "MIGRATING"

    # other status
    RESCUE = "RESCUE"
    REVERT_RESIZE = "REVERT_RESIZE"
    SHELVED = "SHELVED"
    SHELVED_OFFLOADED = "SHELVED_OFFLOADED"
    SOFT_DELETED = "SOFT_DELETED"
    PAUSED = "PAUSED"
    SUSPENDED = "SUSPENDED"
    SHUTOFF = "SHUTOFF"
    REBOOT = "REBOOT"
    HARD_REBOOT = "HARD_REBOOT"
    PASSWORD = "PASSWORD"


OS_SERVER_STATUS_MAPPING: Dict[OpenStackServerStatus, ServerStatus] = {
    # default status
    OpenStackServerStatus.UNKNOWN: ServerStatus.UNKNOWN,
    # fault status
    OpenStackServerStatus.ERROR: ServerStatus.ERROR,
    OpenStackServerStatus.DELETED: ServerStatus.DELETED,
    # progress status
    OpenStackServerStatus.ACTIVE: ServerStatus.ACTIVE,
    OpenStackServerStatus.BUILD: ServerStatus.BUILD,
    OpenStackServerStatus.REBUILD: ServerStatus.REBUILD,
    OpenStackServerStatus.RESIZE: ServerStatus.RESIZE,
    OpenStackServerStatus.VERIFY_RESIZE: ServerStatus.VERIFY_RESIZE,
    OpenStackServerStatus.MIGRATING: ServerStatus.MIGRATING,
    # other status
    OpenStackServerStatus.RESCUE: ServerStatus.RESCUE,
    OpenStackServerStatus.REVERT_RESIZE: ServerStatus.REVERT_RESIZE,
    OpenStackServerStatus.SHELVED: ServerStatus.SHELVED,
    OpenStackServerStatus.SHELVED_OFFLOADED: ServerStatus.SHELVED_OFFLOADED,
    OpenStackServerStatus.SOFT_DELETED: ServerStatus.SOFT_DELETED,
    OpenStackServerStatus.PAUSED: ServerStatus.PAUSED,
    OpenStackServerStatus.SUSPENDED: ServerStatus.SUSPENDED,
    OpenStackServerStatus.SHUTOFF: ServerStatus.SHUTOFF,
    OpenStackServerStatus.REBOOT: ServerStatus.REBOOT,
    OpenStackServerStatus.HARD_REBOOT: ServerStatus.HARD_REBOOT,
    OpenStackServerStatus.PASSWORD: ServerStatus.PASSWORD,
}

OS_SERVER_STATUS_MAPPER = StatusMapper.create(OS_SERVER_STATUS_MAPPING, ServerStatus.UNKNOWN)


class OpenStackVolumeStatus(Enum):
    # main status
    CREATING = 'creating'
    AVAILABLE = 'available'
    IN_USE = 'in-use'
    DELETING = 'deleting'

    # fault status
    ERROR = 'error'
    ERROR_DELETING = 'error_deleting'
    ERROR_MANAGING = 'error_managing'
    ERROR_RESTORING = 'error_restoring'
    ERROR_BACKING_UP = 'error_backing-up'
    ERROR_EXTENDING = 'error_extending'

    # other status
    MANAGING = 'managing'
    ATTACHING = 'attaching'
    DETACHING = 'detaching'
    MAINTENANCE = 'maintenance'
    RESTORING_BACKUP = 'restoring-backup'
    RESERVED = 'reserved'
    AWAITING_TRANSFER = 'awaiting-transfer'
    BACKING_UP = 'backing-up'
    DOWNLOADING = 'downloading'
    UPLOADING = 'uploading'
    RETYPING = 'retyping'
    EXTENDING = 'extending'


OS_VOLUME_STATUS_MAPPING: Dict[OpenStackVolumeStatus, VolumeStatus] = {
    # main status
    OpenStackVolumeStatus.CREATING: VolumeStatus.CREATING,
    OpenStackVolumeStatus.AVAILABLE: VolumeStatus.AVAILABLE,
    OpenStackVolumeStatus.IN_USE: VolumeStatus.IN_USE,
    OpenStackVolumeStatus.DELETING: VolumeStatus.DELETING,
    # fault status
    OpenStackVolumeStatus.ERROR: VolumeStatus.ERROR,
    OpenStackVolumeStatus.ERROR_DELETING: VolumeStatus.ERROR_DELETING,
    OpenStackVolumeStatus.ERROR_MANAGING: VolumeStatus.ERROR_MANAGING,
    OpenStackVolumeStatus.ERROR_RESTORING: VolumeStatus.ERROR_RESTORING,
    OpenStackVolumeStatus.ERROR_BACKING_UP: VolumeStatus.ERROR_BACKING_UP,
    OpenStackVolumeStatus.ERROR_EXTENDING: VolumeStatus.ERROR_EXTENDING,
    # other status
    OpenStackVolumeStatus.MANAGING: VolumeStatus.MANAGING,
    OpenStackVolumeStatus.ATTACHING: VolumeStatus.ATTACHING,
    OpenStackVolumeStatus.DETACHING: VolumeStatus.DETACHING,
    OpenStackVolumeStatus.MAINTENANCE: VolumeStatus.MAINTENANCE,
    OpenStackVolumeStatus.RESTORING_BACKUP: VolumeStatus.RESTORING_BACKUP,
    OpenStackVolumeStatus.RESERVED: VolumeStatus.RESERVED,
    OpenStackVolumeStatus.AWAITING_TRANSFER: VolumeStatus.AWAITING_TRANSFER,
    OpenStackVolumeStatus.BACKING_UP: VolumeStatus.BACKING_UP,
    OpenStackVolumeStatus.DOWNLOADING: VolumeStatus.DOWNLOADING,
    OpenStackVolumeStatus.UPLOADING: VolumeStatus.UPLOADING,
    OpenStackVolumeStatus.RETYPING: VolumeStatus.RETYPING,
    OpenStackVolumeStatus.EXTENDING: VolumeStatus.EXTENDING,
}

OS_VOLUME_STATUS_MAPPER = StatusMapper.create(OS_VOLUME_STATUS_MAPPING, VolumeStatus.UNKNOWN)


class OpenStack(CloudConnector):
    """OpenStack

    Class for encapsulating OpenStack clients and their
    authentication and includes some custom methods for complex
    queries.

    :Attributes:
        * **nova** - :class:`novaclient.client.Client`
        * **allocation** - `nectarallocationclient v1`_
        * **keystone** - :class:`keystoneclient.client.Client`
        * **glance** - :class:`glanceclient.client.Client`
        * **cinder** - :class:`cinderclient.client.Client`
        * **roles** - A list of roles (names) scoped to the authenticated
          user and project.

    .. todo:: Optionally construct object using parameters rather than
              loading environment variables.
    """

    def __init__(self):
        auth, sess = OpenStack._create_auth_session(settings)
        roles = OpenStack._create_roles(auth, sess)
        nova, allocation, keystone, glance, cinder = OpenStack._create_clients(sess)

        self.roles = roles
        self.nova = nova
        self.allocation = allocation
        self.keystone = keystone
        self.glance = glance
        self.cinder = cinder

    @staticmethod
    def _create_auth_session(conf: LazySettings) -> Tuple[ApplicationCredential,
                                                          Session]:
        auth_url = conf.OS_AUTH_URL
        auth_crd_id = conf.OS_APPLICATION_CREDENTIAL_ID
        auth_crd_pw = conf.OS_APPLICATION_CREDENTIAL_SECRET

        auth = ApplicationCredential(auth_url=auth_url,
                                     application_credential_id=auth_crd_id,
                                     application_credential_secret=auth_crd_pw)
        sess = Session(auth=auth)

        return auth, sess

    @staticmethod
    def _create_clients(sess: Session) -> Tuple[nova_client.Client,
                                                allocation_client.Client,
                                                keystone_client.Client,
                                                glance_client.Client,
                                                cinder_client.Client]:
        nova = nova_client.Client('2.31', session=sess)
        allocation = allocation_client.Client('1', session=sess)
        keystone = keystone_client.Client((3, 0), session=sess)
        glance = glance_client.Client('2', session=sess)
        cinder = cinder_client.Client('3', session=sess)

        return nova, allocation, keystone, glance, cinder

    @staticmethod
    def _create_roles(auth: ApplicationCredential,
                      sess: Session) -> AccessInfo:
        auth_ref = auth.get_auth_ref(sess)
        return auth_ref.role_names

    def create_server(self,
                      name: str,
                      flavor: str,
                      volume: Volume,
                      description: Optional[str] = None,
                      metadata: Optional[Dict] = None,
                      userdata: Optional[Dict] = None,
                      networks: Optional[List[Network]] = None,
                      security_groups: Optional[str] = None,
                      key_name: Optional[str] = None,
                      zone: Optional[str] = None) -> Server:
        block_device_mapping = [{
            'source_type': "volume",
            'destination_type': 'volume',
            'delete_on_termination': False,
            'uuid': str(volume.id),
            'boot_index': '0',
        }]

        network_interfaces = None
        if networks is not None:
            # TODO: correct projection
            network_interfaces = list({"net-id": n.id for n in networks})

        try:
            nova_server = self.nova.servers.create(
                name=name,
                image=str(),
                flavor=flavor,
                block_device_mapping_v2=block_device_mapping,
                description=description,
                meta=metadata,
                userdata=userdata,
                nics=network_interfaces,
                security_groups=security_groups,
                key_name=key_name,
                availability_zone=zone)
            nova_server_id = UUID(nova_server.id)
            return Server(id=nova_server_id,
                          name=nova_server.name,
                          description=None,
                          metadata=nova_server.metadata,
                          zone=nova_server.availability_zone)
        except ValueError | nova_exceptions.UnsupportedAttribute:
            raise CloudConnectorError(f"Failed to create server '{name}'")

    def get_server_list(self,
                        search_opts: Dict = None) -> List[Server]:
        nova_servers = self.cinder.servers.list(search_opts)
        return list(map(lambda s: Server(id=UUID(s.id),
                                         name=s.name,
                                         description=s.description,
                                         metadata=s.metadata,
                                         zone=s.availability_zone),
                        nova_servers))

    def _get_nova_server(self,
                         server: Server) -> NovaServer:
        return self.nova.servers.get(server.id)

    def get_server_flavor(self,
                          server: Server) -> Flavor:
        nova_server = self._get_nova_server(server)
        server_flavor_id = nova_server.flavor['id']
        return Flavor(id=UUID(server_flavor_id))

    def get_server_status(self,
                          server: Server) -> ServerStatus:
        nova_server = self._get_nova_server(server)
        return OS_SERVER_STATUS_MAPPER.get_status(nova_server.status)

    def resize_server(self,
                      server: Server,
                      flavor: Flavor) -> Server:
        # TODO: Implement resize server
        nova_server = self._get_nova_server(server)
        resize_result = self.nova.servers.resize(nova_server, flavor)
        return Server()

    def get_server_zone(self,
                        server: Server) -> str:
        nova_server = self._get_nova_server(server)
        return nova_server.availability_zone

    def is_server_created(self,
                          server: Server) -> Optional[bool]:
        try:
            self._get_nova_server(server)
            return True
        except nova_exceptions.NotFound:
            return False
        except nova_exceptions.ClientException:
            return None

    def delete_server(self,
                      server: Server) -> Optional[bool]:
        try:
            nova_server = self._get_nova_server(server)
            self.nova.servers.delete(nova_server)
            logger.exception(f"Nova server {server.id} deleted")
            return True
        except nova_exceptions.NotFound:
            logger.exception(f"Nova server {server.id} already deleted")
            return True
        except nova_exceptions.ClientException:
            logger.exception(f"Nova server delete failed for server {server.id}")
            return None

    def create_volume(self,
                      name: str,
                      size: int,
                      source_volume: Optional[Volume] = None,
                      description: Optional[str] = None,
                      metadata: Optional[Dict] = None,
                      zone: Optional[str] = None,
                      readonly: bool = False,
                      bootable: bool = True) -> Volume:
        """
        Create a bootable cloud volume from available source volume.
        """
        cinder_source_volume_id = None
        metadata_readonly = {"readonly": readonly}
        cinder_metadata = metadata_readonly | metadata
        if source_volume is not None:
            cinder_source_volume_id = source_volume.id

        try:
            cinder_volume = self.cinder.volumes.create(
                name=name,
                size=size,
                source_volid=cinder_source_volume_id,
                description=description,
                metadata=cinder_metadata,
                availability_zone=zone)

            self.cinder.volumes.set_bootable(volume=cinder_volume,
                                             flag=bootable)

            return Volume(id=UUID(cinder_volume.id),
                          name=cinder_volume.name,
                          description=cinder_volume.description,
                          metadata=cinder_volume.metadata,
                          bootable=cinder_volume.bootable,
                          zone=cinder_volume.availability_zone)
        except ValueError | cinder_exceptions.UnsupportedAttribute:
            raise CloudConnectorError(f"Failed to create volume '{name}'")

    def create_volume_backup(self,
                             volume: Volume,
                             name: str,
                             description: Optional[str] = None,
                             metadata: Optional[Dict] = None,
                             zone: Optional[str] = None) -> VolumeBackup:
        """
        Create a backup cloud volume from available source volume.
        """
        cinder_source_volume_id = volume.id
        try:
            cinder_backup_volume = self.cinder.backup.create(
                name=name,
                volume=cinder_source_volume_id,
                description=description,
                metadata=metadata,
                availability_zone=zone)

            return VolumeBackup(id=UUID(cinder_backup_volume.id),
                                name=cinder_backup_volume.name,
                                description=cinder_backup_volume.description,
                                metadata=cinder_backup_volume.metadata,
                                zone=cinder_backup_volume.availability_zone)
        except cinder_exceptions.ClientException | ValueError | cinder_exceptions.UnsupportedAttribute:
            raise CloudConnectorError(f"Failed to create backup volume '{name}'")

    def get_volume_list(self,
                        search_opts: Dict = None) -> List[Volume]:
        cinder_volumes = self.cinder.volumes.list(search_opts)
        return list(map(lambda v: Volume(id=UUID(v.id),
                                         name=v.name,
                                         description=v.description,
                                         metadata=v.metadata,
                                         bootable=v.bootable,
                                         zone=v.availability_zone),
                        cinder_volumes))

    def _get_cinder_volume(self,
                           volume: Volume) -> CinderVolume:
        return self.cinder.volumes.get(volume.id)

    def _get_cinder_volume_backup(self,
                                  backup: VolumeBackup) -> CinderBackup:
        return self.cinder.backup.get(backup.id)

    def get_volume_status(self,
                          volume: Volume) -> VolumeStatus:
        cinder_volume = self._get_cinder_volume(volume)
        return OS_VOLUME_STATUS_MAPPER.get_status(cinder_volume.status)

    def get_volume_zone(self,
                        volume: Volume) -> str:
        cinder_volume = self._get_cinder_volume(volume)
        return cinder_volume.availability_zone

    def is_volume_created(self,
                          volume: Volume) -> Optional[bool]:
        try:
            self._get_cinder_volume(volume)
            return True
        except cinder_exceptions.NotFound:
            return False
        except cinder_exceptions.ClientException:
            return None

    def is_backup_created(self,
                          backup: VolumeBackup) -> Optional[bool]:
        try:
            self._get_cinder_volume_backup(backup)
            return True
        except cinder_exceptions.NotFound:
            return False
        except cinder_exceptions.ClientException:
            return None

    def delete_volume(self,
                      volume: Volume):
        try:
            cinder_volume = self._get_cinder_volume(volume)
            self.cinder.volumes.delete(cinder_volume)
            logger.exception(f"Cinder volume {volume.id} deleted")
            return True
        except cinder_exceptions.NotFound:
            logger.exception(f"Cinder volume {volume.id} already deleted")
            return True
        except cinder_exceptions.ClientException:
            logger.exception(f"Cinder volume delete failed for volume {volume.id}")

    @abstractmethod
    def get_console_info(self,
                         server: Server) -> ConsoleInfo:
        pass


class OpenStackVncServer(OpenStack):

    def get_console_info(self,
                         server: Server) -> ConsoleInfo:
        host_address = str()
        host_port = 5900

        nova_server = self._get_nova_server(server)
        for key in nova_server.addresses:
            host_address = nova_server.addresses[key][0]['addr']

        return ConsoleInfo(protocol=ConsoleProtocol.VNC,
                           host=host_address,
                           port=host_port)


class OpenStackVncHypervisor(OpenStack):

    def get_console_info(self,
                         server: Server) -> ConsoleInfo:
        nova_server = self._get_nova_server(server)
        console_info = self.nova.servers.get_console_url(nova_server, 'novnc')
        console_url = console_info.get('remote_console').get('url')
        console_token = self._get_console_token(console_url)
        connect_info = self._get_connect_info(console_token)

        return ConsoleInfo(protocol=ConsoleProtocol.VNC,
                           host=connect_info.host,
                           port=connect_info.port)

    def _get_console_token(self, console_url) -> Optional[str]:
        return parse.parse_qs(
            parse.urlparse(console_url).query
        ).get('token', ['']).pop()

    def _get_connect_info(self, console_token):
        # TODO: Implement os-console-auth-token API call with nova client
        return {}
