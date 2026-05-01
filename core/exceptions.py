class KMSError(Exception):
    pass

class ConfigError(KMSError):
    pass

class VaultError(KMSError):
    pass

class StorageError(KMSError):
    pass

class LLMError(KMSError):
    pass

class HandlerError(KMSError):
    pass

class PipelineError(KMSError):
    pass
