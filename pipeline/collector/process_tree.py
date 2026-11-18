"""Windows 운영 작업과 하위 프로세스를 같은 종료 단위로 묶는다."""
import os
import threading

_job = None  # 프로세스 종료까지 보유한다. 자식에게 핸들을 상속하지 않는다.


def guard_process_tree():
    """Python 부모 종료와 예약 실행기 PowerShell 종료 모두 하위 작업을 끝낸다."""
    global _job
    if os.name != 'nt' or _job is not None:
        return
    import ctypes
    from ctypes import wintypes as w

    class Limits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', w.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('read', 'write', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

    class Extended(ctypes.Structure):
        _fields_ = [('basic', Limits), ('io', Counters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    k.CreateJobObjectW.restype = w.HANDLE
    k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    handle = k.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not k.AssignProcessToJobObject(handle, k.GetCurrentProcess()):
        error = ctypes.get_last_error()
        k.CloseHandle(handle)
        raise ctypes.WinError(error)
    _job = handle
    supervisor = os.environ.get('MJU_RADAR_SUPERVISOR_PID')
    if supervisor is not None:
        if not supervisor.isascii() or not supervisor.isdigit() or int(supervisor) <= 0 or int(supervisor) == os.getpid():
            raise OSError('잘못된 운영 실행기 PID')
        parent = k.OpenProcess(0x100000, False, int(supervisor))  # SYNCHRONIZE: 종료 대기만 허용
        if not parent:
            raise ctypes.WinError(ctypes.get_last_error())

        def wait_supervisor():
            try:
                if k.WaitForSingleObject(parent, 0xFFFFFFFF) == 0:
                    k.TerminateJobObject(handle, 1)
            finally:
                k.CloseHandle(parent)

        threading.Thread(target=wait_supervisor, name='운영-실행기-종료-감시', daemon=True).start()
