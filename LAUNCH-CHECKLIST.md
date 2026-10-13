# Checklist de lanzamiento · Comexplora

## 1. Publicar

- [ ] Crear el repositorio en GitHub (rama principal: `main`).
- [ ] Subir **todo** el contenido de esta carpeta, incluidas las carpetas ocultas `.github/` y las carpetas `scripts/`, `templates/` y `assets/`.
- [ ] Settings → Pages → Build and deployment → Source: **GitHub Actions**.
- [ ] Settings → Actions → General → Workflow permissions: **Read and write permissions**.
- [ ] Actions → «Actualizar y publicar Comexplora» → **Run workflow**.
- [ ] En el resumen de la ejecución, revisar qué fuentes respondieron (✔/✘). Si alguna falla siempre, desactivarla en `sources.json` (`"enabled": false`).
- [ ] Abrir el sitio y una nota individual; comprobar que se ven las fotos.

## 2. Dominio

Hoy el sitio está en https://comexplora.github.io (repositorio `comexplora/comexplora.github.io`). La dirección se detecta sola: `scripts/build_pages.py` la toma del archivo `CNAME` si existe y, si no, del nombre del repositorio. Canónicas, sitemaps, RSS, `robots.txt` y Open Graph se regeneran con esa dirección; no hace falta tocar código.

- [ ] Comprar `comexplora.com` y, si está libre, `comexplora.com.ar` (NIC Argentina).
- [ ] Verificar el dominio en GitHub (Settings de tu cuenta → Pages → Add a domain) antes de apuntar el DNS.
- [ ] Configurar el DNS según las instrucciones de GitHub Pages (registros A/AAAA del dominio raíz y CNAME de `www` hacia `comexplora.github.io`).
- [ ] Crear en la raíz del repositorio el archivo `CNAME` con una sola línea: `comexplora.com`. Cargar el mismo dominio en Settings → Pages → Custom domain y activar «Enforce HTTPS».
- [ ] Disparar el workflow a mano (Actions → «Actualizar y publicar Comexplora» → Run workflow) para regenerar todas las páginas con la dirección nueva. GitHub redirige solo comexplora.github.io al dominio propio.
- [ ] Actualizar las tareas programadas (curaduría y control semanal) con la dirección nueva.

## 3. Datos propios

- [ ] Crear un correo con el dominio propio (por ejemplo `contacto@comexplora.com`) y cargarlo en `site.json` → `contactEmail`.
- [ ] Cargar las redes sociales en `CONFIG.social` (`assets/app.js`) cuando existan.
- [ ] Revisar la Política de privacidad y los Términos (sección `PAGES` en `assets/app.js`).

## 4. Buscadores

- [ ] Google Search Console: verificar el dominio.
- [ ] Enviar `https://comexplora.com/sitemap.xml` y `https://comexplora.com/news-sitemap.xml` (una vez conectado el dominio).
- [ ] Inspeccionar la portada y una nota curada; validar el marcado `NewsArticle` con la prueba de resultados enriquecidos.
- [ ] Probar cómo se ve un enlace compartido (por ejemplo, con el inspector de publicaciones de LinkedIn o el depurador de Facebook).

## Mantenimiento

- GitHub pausa los workflows programados de repositorios públicos sin actividad durante 60 días. Como cada corrida guarda cambios, no debería pasar; si pasa, se reactiva desde la pestaña Actions.
- La automatización recopila y atribuye; no reemplaza la revisión editorial. Las notas importantes conviene cargarlas como curadas en `data/news.json`.
