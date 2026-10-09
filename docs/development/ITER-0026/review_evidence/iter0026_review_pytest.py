import socket, sys
attempts = []
def blocked(name):
    def deny(*args, **kwargs):
        attempts.append(name)
        raise AssertionError('review network blocked: ' + name)
    return deny
for name in ('connect', 'connect_ex'):
    setattr(socket.socket, name, blocked('socket.' + name))
for name in ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'create_connection'):
    setattr(socket, name, blocked(name))
import pytest
code = pytest.main(sys.argv[1:] + ['-p', 'no:cacheprovider'])
print('REVIEW_NETWORK_ATTEMPTS', attempts)
sys.exit(code if not attempts else 99)
