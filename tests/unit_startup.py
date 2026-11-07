"""Inicio con Windows (menú de la bandeja): startup_get/startup_set/_startup_cmd de verdad,
contra un "winreg" simulado en memoria con la misma interfaz (este sandbox es Linux: no hay
winreg real, pero la lógica de camui.py es la misma que correría contra el registro de verdad;
el "import winreg" está dentro de cada función, así que basta con poner el módulo falso en
sys.modules antes de llamarlas)."""
import sys
import os
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeKey:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeWinReg(types.ModuleType):
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 1
    REG_SZ = 1

    def __init__(self):
        super().__init__("winreg")
        self.data = {}

    def OpenKey(self, hive, subkey, reserved=0, access=0):
        return _FakeKey(self.data.setdefault(subkey, {}))

    def QueryValueEx(self, key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], 1

    def SetValueEx(self, key, name, reserved, type_, value):
        key.store[name] = value

    def DeleteValue(self, key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]


fake = _FakeWinReg()
sys.modules["winreg"] = fake
import camui                                                         # noqa: E402  (tras el winreg falso)

# 1) nada puesto todavía -> False ------------------------------------------------------------
assert camui.startup_get() is False
print("1) sin nada puesto -> False OK")

# 2) activarlo: queda el comando exacto para relanzar cam-ui, y get() pasa a True ------------
ok, err = camui.startup_set(True)
assert ok, err
assert camui.startup_get() is True
saved = fake.data[camui.STARTUP_KEY][camui.STARTUP_NAME]
assert saved == camui._startup_cmd(), saved
assert camui.APP_FILE in saved, saved
print("2) activarlo guarda el comando real de relanzar cam-ui OK")

# 3) "se reinstaló en otra carpeta": el valor guardado ya no es el comando actual -> False ---
fake.data[camui.STARTUP_KEY][camui.STARTUP_NAME] = '"C:\\otra\\pythonw.exe" "C:\\otra\\camui.py"'
assert camui.startup_get() is False, "una ruta distinta a la actual debe contar como «no puesto»"
print("3) ruta obsoleta (tras mover/reinstalar) -> no cuenta como puesto OK")

# 4) activarlo de nuevo lo corrige solo (se sobrescribe con la ruta actual) ------------------
ok, err = camui.startup_set(True)
assert ok and camui.startup_get() is True, err
print("4) volver a activarlo se autocorrige OK")

# 5) desactivarlo borra el valor; desactivarlo otra vez (ya borrado) no debe fallar ----------
ok, err = camui.startup_set(False)
assert ok, err
assert camui.startup_get() is False
assert camui.STARTUP_NAME not in fake.data[camui.STARTUP_KEY]
ok2, err2 = camui.startup_set(False)
assert ok2, err2                      # false->false (ya no estaba): no debe lanzar FileNotFoundError
print("5) desactivarlo borra la entrada; desactivarlo dos veces no falla OK")

print("TODO OK unit_startup")
