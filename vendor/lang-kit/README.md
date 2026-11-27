<!-- README.md -->
# lang-kit (copia)

Copia de [lang-kit](https://github.com/enderHugin/lang-kit), el sistema de idiomas común. Sirve para ejecutar cam-ui desde el código fuente, sin instalar.

Al instalarlo, py-installer pone su propia copia de `langkit.py` y de los diccionarios comunes junto al programa. Para actualizar esta copia:

```bash
cp ../lang-kit/langkit.py ../lang-kit/VERSION vendor/lang-kit/ && cp ../lang-kit/lang/common.*.json vendor/lang-kit/lang/
cp ../lang-kit/langkit.js site/
```
