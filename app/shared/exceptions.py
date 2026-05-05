"""Excepciones de dominio compartidas."""


class TalentRefineError(Exception):
    """Base de todas las excepciones de la aplicación."""


class DocumentNotFoundError(TalentRefineError):
    pass


class StorageError(TalentRefineError):
    pass


class ProcessingError(TalentRefineError):
    pass


class VectorStoreError(TalentRefineError):
    pass


class LLMError(TalentRefineError):
    pass
