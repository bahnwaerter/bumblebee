import abc


def get_cloud_environment():
    if not hasattr(get_cloud_environment, 'cloud_environment'):
        get_cloud_environment.cloud_environment = CloudEnvironment()
    return get_cloud_environment.cloud_environment


class CloudEnvironment(object):

    @abc.abstractmethod
    def generate_server_name(self, username, desktop_id) -> str:
        pass

    @abc.abstractmethod
    def generate_hostname(self, hostname_id, desktop_id) -> str:
        pass

    @abc.abstractmethod
    def get_domain(self, user) -> str:
        pass

    @abc.abstractmethod
    def after_time(self, seconds):
        pass

    @abc.abstractmethod
    def generate_password(self) -> str:
        pass


class FlavorDetails(object):

    def __init__(self, flavor):
        self.id = flavor.id
        self.name = flavor.name
        self.ram = int(flavor.ram) / 1024
        self.disk = flavor.disk
        self.vcpus = flavor.vcpus