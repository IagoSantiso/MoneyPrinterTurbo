# Subir fotos de TVs a Cloudflare R2 desde el móvil (guía para dummies)

Ejemplo real usado aquí: **Xiaomi TV F Pro 43** (la misma TV del vídeo de
prueba). Prefijo que vamos a usar: **`XIAOMI_TV_F_PRO_43/`**

No hace falta ninguna app — todo se hace desde el navegador del móvil en
**dash.cloudflare.com**. Si el sitio se ve raro/apretado, activa "Ver
como escritorio" en el menú del navegador (⋮ en Chrome, "aA" en Safari).

## 1. Crear el bucket (una sola vez)

1. Entra en **dash.cloudflare.com** y haz login.
2. Menú lateral (icono ☰ si no lo ves) → **R2 Object Storage**.
3. Botón **Create bucket**.
4. Nombre del bucket: por ejemplo `tv-affiliate-media` (minúsculas, sin
   espacios ni tildes). Location: "Automatic". → **Create bucket**.

Ya tienes el bucket. No hace falta crear carpetas a mano: se crean solas
al subir el primer archivo con esa ruta.

## 2. Subir las fotos de la Xiaomi TV F Pro 43

1. Dentro del bucket que acabas de crear, botón **Upload**.
2. Verás un botón para subir archivos y otro para crear una carpeta antes
   de subir. Crea (o escribe al subir) la carpeta:
   **`XIAOMI_TV_F_PRO_43`**
3. Dentro de esa carpeta, sube tus fotos **renombrándolas con un número
   por delante** para controlar el orden en el vídeo:
   - `01_pantalla.jpg` → primer plano, la pantalla encendida
   - `02_diseno.jpg` → la tele de frente/perfil
   - `03_panel.jpg` → detalle de bordes/panel
   - `04_mando.jpg` → el mando a distancia
   - (los que quieras, siguen el mismo patrón `05_`, `06_`...)
4. Para renombrar antes de subir desde el móvil: mantén pulsada la foto en
   tu galería → Renombrar (Android) o usa la app Archivos/Files (iPhone)
   para renombrarla antes de seleccionarla en el selector de subida de
   Cloudflare.

Cuando termines, la ruta completa de cada archivo debe verse así en el
bucket: `XIAOMI_TV_F_PRO_43/01_pantalla.jpg`, etc.

## 3. Crear el token de API (para que el pipeline pueda leer las fotos)

Aunque las subas a mano, el pipeline necesita una llave para poder leerlas.

1. R2 → **Manage R2 API Tokens** (botón arriba a la derecha de la lista
   de buckets).
2. **Create API Token**.
3. Nombre: `moneyprinterturbo-pipeline`.
4. Permisos: **Object Read & Write** (o solo "Object Read" si vas a subir
   siempre a mano desde el dashboard y nunca desde el script).
5. **Specify bucket(s)** → selecciona solo `tv-affiliate-media` (nunca dejes
   el token con acceso a todos los buckets).
6. **Create API Token**.
7. Cloudflare te muestra 3 datos — **cópialos ya, el Secret solo se ve
   una vez**:
   - **Access Key ID**
   - **Secret Access Key**
   - El **Account ID** lo ves en la propia pantalla de R2 Overview
     (columna derecha), no hace falta crearlo.

## 4. Pásamelos y actualiza el Sheet

Dame estos 4 datos (los trato como confidenciales, van directos a
`config.toml`, que nunca se sube a git):

- Account ID
- Access Key ID
- Secret Access Key
- Nombre del bucket (`tv-affiliate-media` en el ejemplo)

Y en tu Google Sheet, en la fila de la **Xiaomi TV F Pro 43**, añade en la
columna `product_images_prefix` (créala si no existe):

```
XIAOMI_TV_F_PRO_43/
```

(con la barra `/` al final). En cuanto tenga las credenciales, actualizo
`config.toml` y relanzamos el vídeo de esta TV — esta vez con tus fotos
reales en vez de stock de Pexels.
