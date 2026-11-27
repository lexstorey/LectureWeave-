"""Store LectureWeave+ secrets in the current user's Windows Credential Manager."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os


CREDENTIAL_TARGET = "LectureWeavePlus/HuggingFaceToken"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


def _advapi32():
    if os.name != "nt":
        raise OSError("Windows Credential Manager is only available on Windows.")
    library = ctypes.WinDLL("advapi32", use_last_error=True)
    library.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    library.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(PCREDENTIALW),
    ]
    library.CredReadW.restype = wintypes.BOOL
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    library.CredFree.restype = None
    return library


def save_huggingface_token(token: str) -> None:
    token = token.strip()
    if not token.startswith("hf_") or len(token) < 10:
        raise ValueError("Enter a valid Hugging Face token beginning with hf_.")
    blob = token.encode("utf-16-le")
    blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = CREDENTIAL_TARGET
    credential.Comment = "LectureWeave+ Hugging Face read token"
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "LectureWeave+"
    library = _advapi32()
    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def load_huggingface_token() -> str:
    """Return the saved token, falling back to the legacy HF_TOKEN variable."""
    try:
        library = _advapi32()
        pointer = PCREDENTIALW()
        if library.CredReadW(
            CREDENTIAL_TARGET, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            try:
                credential = pointer.contents
                raw = ctypes.string_at(
                    credential.CredentialBlob, credential.CredentialBlobSize
                )
                return raw.decode("utf-16-le").strip()
            finally:
                library.CredFree(pointer)
        error = ctypes.get_last_error()
        if error != 1168:  # ERROR_NOT_FOUND
            raise ctypes.WinError(error)
    except OSError:
        if os.name == "nt":
            raise
    return os.environ.get("HF_TOKEN", "").strip()


def delete_huggingface_token() -> bool:
    """Remove the saved Credential Manager entry; return whether one existed."""
    library = _advapi32()
    if library.CredDeleteW(CREDENTIAL_TARGET, CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == 1168:  # ERROR_NOT_FOUND
        return False
    raise ctypes.WinError(error)

