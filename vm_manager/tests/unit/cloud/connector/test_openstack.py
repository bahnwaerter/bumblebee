from unittest.mock import patch, MagicMock

from unittest import TestCase

from vm_manager.cloud.connector.objects import Server, ConsoleInfo
from vm_manager.cloud.connector.openstack import OpenStack
from vm_manager.tests.fakes import Fake

from novaclient.v2.servers import ServerManager
from novaclient.v2.servers import Server as NovaServer


class FakeOpenStack(OpenStack):
    """
    Mocked OpenStack cloud connector implementation for testing purposes.

    This cloud connector overrides all abstract methods for testing purposes
    but does not implement these methods, e.g. the method get_console_info.
    """

    @patch.object(OpenStack, '_create_clients',
                  return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()))
    @patch.object(OpenStack, '_create_roles',
                  return_value=MagicMock())
    @patch.object(OpenStack, '_create_auth_session',
                  return_value=(MagicMock(), MagicMock()))
    def __init__(self,
                 mfunc__create_auth_session,
                 mfunc__create_roles,
                 mfunc__create_clients):
        super().__init__()

    def get_console_info(self,
                         server: Server) -> ConsoleInfo:
        pass


class OpenStackTests(TestCase):

    def setUp(self):
        self.openstack = FakeOpenStack()

    @patch.object(ServerManager, 'create',
                  return_value={"server": {
                      "id": str(Fake.server1.id),
                      "name": Fake.server1.name
                  }})
    def test_create_server(self, mfunc_create_server):
        actual_server = self.openstack.create_server(Fake.server1.name,
                                                     Fake.flavor_small.name,
                                                     Fake.volume1,
                                                     Fake.zone)
        mfunc_create_server.assert_called_once()
        self.assertEqual(Fake.server1, actual_server)

    @patch.object(ServerManager, 'list',
                  return_value={"servers": [
                      {
                          "id": str(Fake.server1.id)
                      }, {
                          "id": str(Fake.server2.id)
                      }
                  ]})
    def test_get_server_list(self, mfunc_get_servers_list):
        actual_server_list = self.openstack.get_server_list()
        mfunc_get_servers_list.assert_called_once()
        self.assertEqual(Fake.server_list, actual_server_list)

    @patch.object(ServerManager, 'get',
                  return_value=NovaServer(manager=ServerManager, info={}))
    def test__get_nova_server(self, mfunc__get_nova_server):
        actual_server = self.openstack._get_nova_server(Fake.server1)
        mfunc__get_nova_server.assert_called_once_with(Fake.server1)
        self.assertEqual(NovaServer(manager=ServerManager, info={}), actual_server)

    def test_get_server_flavor(self):
        pass

    def test_get_server_status(self):
        pass

    def test_resize_server(self):
        pass

    def test_get_server_zone(self):
        pass

    def test_create_volume(self):
        pass

    def test_get_volume_list(self):
        pass

    def test_get_volume_status(self):
        pass

    def test_get_volume_zone(self):
        pass

    def test_delete_volume(self):
        pass

    def test_get_console_info(self):
        pass
