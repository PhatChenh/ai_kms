from core.exceptions import KMSError, ConfigError, VaultError
from core.result import Result, Failure

def check_error(_input: str) -> Result[str]:
    if _input == 'config':
        raise ConfigError("bad config")
    if _input == 'vault':
        raise VaultError("bad vault path")


try:
    error = input("What do you want?")
    check_error(error)
except KMSError as e:
    # print(type(e).__name__)
    # print(e)
    fail = Failure(
        error = str(e),
        recoverable=True,
        context={}
    )
    # fail.unwrap()

print(fail.error)
print(fail.traceback)