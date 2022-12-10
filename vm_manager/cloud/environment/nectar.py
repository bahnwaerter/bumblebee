from datetime import datetime, timedelta

from django.utils.crypto import get_random_string
from django.utils.timezone import utc

from vm_manager.cloud.environment.environment import CloudEnvironment


class Nectar(CloudEnvironment):

    def generate_server_name(self, username, desktop_id) -> str:
        return f"{username}_{desktop_id}"

    def generate_hostname(self, hostname_id, desktop_id) -> str:
        return f"vd{desktop_id[0]}-{hostname_id}"

    def get_domain(self, user) -> str:
        return 'test'

    def after_time(self, seconds):
        return datetime.now(utc) + timedelta(seconds=seconds)

    def generate_password(self) -> str:
        return get_random_string(20)
