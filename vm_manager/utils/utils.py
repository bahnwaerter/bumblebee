from abc import abstractmethod
from datetime import datetime, timedelta
from enum import Enum
import logging

from cinderclient import client as cinder_client
from glanceclient import client as glance_client
from keystoneauth1.identity.v3 import ApplicationCredential
from keystoneauth1 import session
from keystoneclient import client as keystone_client
from nectarallocationclient import client as allocation_client
from novaclient import client as nova_client
from urllib import parse as urlparse

from django.conf import settings
from django.utils.crypto import get_random_string
from django.utils.timezone import utc
from novaclient.v2.servers import ServerManager


class ServerManagerConsoleToken(ServerManager):
    """ServerManagerConsoleToken

    Extended ServerManager class of the OpenStack Nova client v2 for
    implementing the available Nova API endpoint '/os-console-auth-tokens'.
    """
    def get_console_auth_token_info(self, console_token):
        """
        Requests console connection information for specified token
        """
        url = '/os-console-auth-tokens/%s' % console_token
        resp, body = self.api.client.get(url)
        return self.convert_into_with_meta(body, resp)

    @staticmethod
    def parse_console_auth_token(access_url, console_type):
        """
        Helper function to parse the console token from a console access url.
        """
        def _get_url_param_value(url, param):
            parsed_url = urlparse.urlparse(url).query
            url_params = urlparse.parse_qs(parsed_url)
            return url_params.get(param, ['']).pop()

        if console_type == 'novnc':
            # URL parsing for noVNC console token URLs where the token
            # parameter is encoded into the path parameter
            url = _get_url_param_value(access_url, 'path')
        else:
            # URL parsing for all other console token URLs where the token
            # parameter is encoded as usual
            url = access_url

        return _get_url_param_value(url, 'token')


class Nectar(object):
    """Nectar

    Class for encapsulating Nectar OpenStack clients and their
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
        auth = ApplicationCredential(
            auth_url=settings.OS_AUTH_URL,
            application_credential_secret=(
                settings.OS_APPLICATION_CREDENTIAL_SECRET),
            application_credential_id=settings.OS_APPLICATION_CREDENTIAL_ID)
        sess = session.Session(auth=auth)

        # Roles
        auth_ref = auth.get_auth_ref(sess)
        self.roles = auth_ref.role_names

        # Establish clients
        self.nova = nova_client.Client('2', session=sess)
        # Patch the official Nova ServerManager with the extended one
        self.nova.servers = ServerManagerConsoleToken(self)
        self.allocation = allocation_client.Client('1', session=sess)
        self.keystone = keystone_client.Client('3', session=sess)
        self.glance = glance_client.Client('2', session=sess)
        self.cinder = cinder_client.Client('3', session=sess)

    @abstractmethod
    def get_console_connection(self, server_id):
        pass

    @abstractmethod
    def get_console_protocol(self):
        pass


class NectarConsoleOpenStackNative(Nectar):
    def get_console_connection(self, server_id):
        console = self.nova.servers.get_vnc_console(server_id)
        access_url = console.get('access_url')
        console_type = console.get('console_type')
        token = ServerManagerConsoleToken.parse_console_auth_token(
            access_url, console_type)
        connection_info = self.nova.servers.get_console_auth_token_info(token)
        return connection_info.get('host'), connection_info.get('port')

    def get_console_protocol(self):
        return 'vnc'


class NectarConsoleInstanceBuiltin(Nectar):
    def get_console_connection(self, server_id):
        nova_server = self.nova.servers.get(server_id)
        ip_address = None
        for key in nova_server.addresses:
            ip_address = nova_server.addresses[key][0]['addr']
        return ip_address, 5900

    def get_console_protocol(self):
        return 'rdp'


def get_nectar():
    if not hasattr(get_nectar, 'nectar'):
        console_server = settings.OS_CONSOLE_SERVER
        if console_server == 'openstack_hypervisor':
            get_nectar.nectar = NectarConsoleOpenStackNative()
        elif console_server == 'instance_builtin':
            get_nectar.nectar = NectarConsoleInstanceBuiltin()
        else:
            raise NotImplementedError
    return get_nectar.nectar


def generate_server_name(username, desktop_id):
    return f"{username}_{desktop_id}"


def generate_hostname(hostname_id, desktop_id) -> str:
    return f"vd{desktop_id[0]}-{hostname_id}"


def get_domain(user) -> str:
    return 'test'


class FlavorDetails(object):

    def __init__(self, flavor):
        self.id = flavor.id
        self.name = flavor.name
        self.ram = int(flavor.ram) / 1024
        self.disk = flavor.disk
        self.vcpus = flavor.vcpus


def after_time(seconds):
    return datetime.now(utc) + timedelta(seconds=seconds)


def generate_password() -> str:
    return get_random_string(20)
