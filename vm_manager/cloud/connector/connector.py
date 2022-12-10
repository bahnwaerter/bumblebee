from abc import abstractmethod
from typing import Optional, Dict, List

from django.conf import settings

from vm_manager.cloud.connector.objects import Volume, Server, ConsoleInfo, \
    ServerStatus, VolumeStatus, Flavor, \
    VolumeBackup, Network
from vm_manager.cloud.connector.openstack import OpenStackVncServer, OpenStackVncHypervisor

__CLOUD_CONNECTORS__ = {
    'vm_manager.cloud.connector.openstack.OpenstackVncServer': OpenStackVncServer,
    'vm_manager.cloud.connector.openstack.OpenstackVncHypervisor': OpenStackVncHypervisor
}


class CloudConnector(object):

    @abstractmethod
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
        pass

    @abstractmethod
    def get_server_list(self,
                        search_opts: Dict = None) -> List[Server]:
        pass

    @abstractmethod
    def get_server_flavor(self,
                          server: Server) -> Flavor:
        pass

    @abstractmethod
    def get_server_status(self,
                          server: Server) -> ServerStatus:
        pass

    @abstractmethod
    def resize_server(self,
                      server: Server,
                      flavor: Flavor) -> Server:
        pass

    @abstractmethod
    def stop_server(self,
                    server: Server) -> bool:
        pass

    @abstractmethod
    def get_server_zone(self,
                        server: Server) -> str:
        pass

    @abstractmethod
    def is_server_created(self,
                          server: Server) -> Optional[bool]:
        pass

    @abstractmethod
    def delete_server(self,
                      server: Server) -> Optional[bool]:
        pass

    @abstractmethod
    def create_volume(self,
                      name: str,
                      size: int,
                      source_volume: Optional[Volume] = None,
                      description: Optional[str] = None,
                      metadata: Optional[Dict] = None,
                      zone: Optional[str] = None,
                      readonly: bool = False,
                      bootable: bool = True) -> Volume:
        pass

    @abstractmethod
    def create_volume_backup(self,
                             volume: Volume,
                             name: str) -> VolumeBackup:
        pass

    @abstractmethod
    def get_volume_list(self,
                        search_opts: Dict = None) -> List[Volume]:
        pass

    @abstractmethod
    def get_volume_status(self,
                          volume: Volume) -> VolumeStatus:
        pass

    @abstractmethod
    def get_volume_zone(self,
                        volume: Volume) -> str:
        pass

    @abstractmethod
    def is_volume_created(self,
                          volume: Volume) -> Optional[bool]:
        pass

    @abstractmethod
    def is_backup_created(self,
                          backup: VolumeBackup) -> Optional[bool]:
        pass

    @abstractmethod
    def delete_volume(self,
                      volume: Volume):
        pass

    @abstractmethod
    def get_console_info(self,
                         server: Server) -> ConsoleInfo:
        pass


def get_cloud_connector() -> CloudConnector:
    if not hasattr(get_cloud_connector, 'cloud_connector'):
        cloud_connector = settings.CLOUD_CONNECTOR
        get_cloud_connector.cloud_connector = __CLOUD_CONNECTORS__.get(cloud_connector)()
    return get_cloud_connector.cloud_connector
