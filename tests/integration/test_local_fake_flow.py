"""Compatibilidad: el flujo local_fake vive en tests/integration/local_fake.

El test antiguo dependia de un stack ya levantado, disparaba processing dos veces y no
guardaba evidencias. La suite mantenida se ejecuta con:

    pytest tests/integration/local_fake -m integration -v
"""
