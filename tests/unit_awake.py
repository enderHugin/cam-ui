"""KeepAwake: mientras cam-ui tiene algo en marcha, Windows no debe suspender el equipo (tampoco
con la sesión bloqueada). Sin Windows de verdad: se sustituye kernel32 por uno de mentira que
apunta cada llamada, y se comprueba qué se le pide y cuándo -- incluida la estructura
REASON_CONTEXT real que monta ctypes (el motivo que enseña «powercfg /requests»)."""
import os
import sys
import ctypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

KA = camui.KeepAwake


class _Fn:
    """Función de kernel32 de mentira: admite restype/argtypes como las de ctypes."""
    def __init__(self, name, k):
        self.name, self.k = name, k

    def __call__(self, *a):
        return self.k.call(self.name, *a)


class FakeKernel32:
    def __init__(self, create_ok=True, set_ok=True):
        self.calls, self.create_ok, self.set_ok = [], create_ok, set_ok
        self.reason = None
        for n in ("PowerCreateRequest", "PowerSetRequest", "PowerClearRequest", "CloseHandle",
                  "SetThreadExecutionState"):
            setattr(self, n, _Fn(n, self))

    def call(self, name, *a):
        if name == "PowerCreateRequest":
            rc = a[0]._obj                        # la REASON_CONTEXT de verdad (ctypes.byref)
            self.reason = (rc.Version, rc.Flags, rc.Reason.SimpleReasonString)
            self.calls.append((name,))
            return 0x1234 if self.create_ok else ctypes.c_void_p(-1).value
        self.calls.append((name,) + tuple(a))
        if name in ("PowerSetRequest",):
            return 1 if self.set_ok else 0
        return 1


def run(fake):
    real_platform, real_windll = camui.sys.platform, getattr(ctypes, "WinDLL", None)
    camui.sys.platform = "win32"
    ctypes.WinDLL = lambda name, use_last_error=False: fake
    try:
        ka = KA()
        ka.set(True)
        on1, how1 = ka.on, ka.how
        n1 = len(fake.calls)
        ka.set(True)                              # ya estaba: no repite nada
        assert len(fake.calls) == n1, fake.calls
        ka.set(False)
        on2, how2 = ka.on, ka.how
        ka.set(True)
        ka.close()
        return on1, how1, on2, how2
    finally:
        camui.sys.platform = real_platform
        if real_windll is None:
            del ctypes.WinDLL
        else:
            ctypes.WinDLL = real_windll


# 1) lo normal: una petición con nombre, «sistema» + «ejecución»; se suelta al parar; se cierra
f = FakeKernel32()
on1, how1, on2, how2 = run(f)
assert (on1, how1, on2) == (True, "request", False), (on1, how1, on2, how2)
assert f.reason == (0, 1, KA.REASON) and "cam-ui" in f.reason[2], f.reason
c = f.calls
assert c[0] == ("PowerCreateRequest",) and c.count(("PowerCreateRequest",)) == 1, c
assert ("PowerSetRequest", 0x1234, 1) in c and ("PowerSetRequest", 0x1234, 3) in c, c
assert ("PowerClearRequest", 0x1234, 1) in c and ("PowerClearRequest", 0x1234, 3) in c, c
assert c[-1] == ("CloseHandle", 0x1234), c
assert not any(x[0] == "SetThreadExecutionState" for x in c), c
i_set, i_clr = c.index(("PowerSetRequest", 0x1234, 1)), c.index(("PowerClearRequest", 0x1234, 1))
assert i_set < i_clr, c
print("1) petición de energía con motivo «{0}»: se pide al arrancar, se suelta al parar OK".format(
    f.reason[2]))

# 2) si no se puede crear la petición: SetThreadExecutionState (y se deshace al parar)
f = FakeKernel32(create_ok=False)
on1, how1, on2, how2 = run(f)
assert (on1, how1, on2, how2) == (True, "execstate", False, ""), (on1, how1, on2, how2)
es = [x[1] for x in f.calls if x[0] == "SetThreadExecutionState"]
assert es[:2] == [0x80000001, 0x80000000], es
print("2) sin petición de energía: SetThreadExecutionState(CONTINUOUS|SYSTEM_REQUIRED) y luego "
      "CONTINUOUS OK")

# 3) la petición existe pero Windows no la acepta: también SetThreadExecutionState
f = FakeKernel32(set_ok=False)
on1, how1, on2, how2 = run(f)
assert (on1, how1) == (True, "execstate"), (on1, how1)
print("3) petición rechazada: cae en SetThreadExecutionState OK")

# 4) fuera de Windows: no hace nada, ni falla
ka = KA()
ka.set(True)
assert ka.on and ka.how == ""
ka.close()
assert not ka.on
print("4) fuera de Windows no hace nada OK")
print("TODO OK unit_awake")
