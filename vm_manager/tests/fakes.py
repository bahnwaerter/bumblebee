from typing import Dict, List, Optional
from unittest.mock import Mock
from uuid import UUID

from vm_manager.cloud.connector.connector import CloudConnector
from vm_manager.cloud.connector.objects import Server, Volume, VolumeStatus, Flavor, ServerStatus, ConsoleInfo


# FLAVORS = [
#     FakeFlavor(id=uuid.uuid4(), name='m3.medium',
#                ram='1', disk='1', vcpus='1'),
#     FakeFlavor(id=uuid.uuid4(), name='m3.xxlarge',
#                ram='2', disk='2', vcpus='2'),
# ]
#
# VOLUMES = [
#     FakeVolume(id='1', name='m3.medium', ram='1', disk='1', vcpus='1'),
# ]


class Fake(object):
    uuid1: UUID = UUID('fa13b725-635a-4f8c-9f40-6b6c85642500')
    uuid2: UUID = UUID('4aa4d0c9-9ed5-4e3c-85fa-bbf8e483bd45')

    zone: str = str('fake_nova_zone')

    server1: Server = Server(id=uuid1,
                             name='fake_server_one',
                             description=None,
                             metadata={},
                             zone=zone)

    server2: Server = Server(id=uuid2,
                             name='fake_server_two',
                             description=None,
                             metadata={},
                             zone=zone)

    server_list: List[Server] = [server1, server2]

    flavor_small: Flavor = Flavor(id=uuid1,
                                  name='fake_flavor_small',
                                  description=None,
                                  metadata={},
                                  zone=zone,
                                  ram_size=None,
                                  disk_size=None,
                                  num_vcpus=None)

    flavor_big: Flavor = Flavor(id=uuid2,
                                name='fake_flavor_big',
                                description=None,
                                metadata={},
                                zone=zone,
                                ram_size=None,
                                disk_size=None,
                                num_vcpus=None)

    flavor_list: List[Flavor] = [flavor_small, flavor_big]

    volume1: Volume = Volume(id=uuid1,
                             name='fake_volume_one',
                             description=None,
                             metadata={},
                             zone=zone,
                             bootable=True)

    volume2: Volume = Volume(id=uuid2,
                             name='fake_volume_two',
                             description=None,
                             metadata={},
                             zone=zone,
                             bootable=True)

    volume_list: List[Volume] = [volume1, volume2]


class FakeCloudConnector(CloudConnector):

    def create_server(self,
                      name: str,
                      flavor: str,
                      volume: Volume,
                      description: Optional[str] = None,
                      metadata: Optional[Dict] = None,
                      userdata: Optional[Dict] = None,
                      network_interfaces: Optional[str] = None,
                      security_groups: Optional[str] = None,
                      key_name: Optional[str] = None,
                      zone: Optional[str] = None) -> Server:
        return Fake.server1

    def get_server_status(self,
                          server: Server) -> ServerStatus:
        pass

    def resize_server(self,
                      server: Server,
                      flavor: Flavor) -> Server:
        pass

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

    def get_volume_list(self,
                        search_opts: Dict = None) -> List[Volume]:
        pass

    def get_volume_status(self,
                          volume: Volume) -> VolumeStatus:
        pass

    def get_volume_zone(self,
                        volume: Volume) -> str:
        pass

    def delete_volume(self,
                      volume: Volume):
        pass

    def get_console_info(self,
                         server: Server) -> ConsoleInfo:
        pass

    def __init__(self):
        self.nova = Mock()
        self.nova.flavors.list = Mock(return_value=FLAVORS)
        self.allocation = Mock()
        self.keystone = Mock()
        self.glance = Mock()

        self.cinder = Mock()
        self.cinder.volumes.list = Mock(return_value=VOLUMES)
        self.cinder.volumes.create = Mock(
            return_value=FakeVolume(id=UUID_1))
