# Geocodificador de Cuba · instalación sin internet

El geocodificador convierte direcciones cubanas en puntos del mapa ("Calle 23 e/
L y M, Vedado", "Hotel Nacional", "Calle Martí #112, Santa Clara") y dice con
cuánta precisión lo hizo. Funciona con toda Cuba y **no necesita internet en
ningún momento**. El mapa, las calles, los portales y el grafo de rutas van
dentro de esta carpeta.

## Qué hay en esta carpeta

| Archivo | Para qué |
|---|---|
| `geocoder-docker.tar.gz` | La aplicación con los datos de toda Cuba (imagen Docker). |
| `install.sh` | Instala y arranca el geocodificador. |
| `docker-compose.yml.tmpl` | Plantilla que usa `install.sh`. No hace falta tocarla. |
| `install-docker.sh` + `docker-offline-*.tar.gz` | Instalan Docker **sin internet**, solo si la máquina no lo tiene. |
| `ejemplo-negocios.xlsx` | Excel de prueba con 5000 negocios ficticios en direcciones reales. |
| `README.md` | Este documento. |

## Requisitos

- Linux de 64 bits (x86_64): Ubuntu, Debian, Fedora…
- 4 GB de RAM y 3 GB libres en disco.
- Permisos de administrador (`sudo`).
- Docker con `docker compose` v2. Si no lo tiene, el paso 1 lo instala desde esta
  misma carpeta.

## Instalar

Abre una terminal **dentro de esta carpeta**. Es mejor copiarla antes al disco
duro y no ejecutarla desde el USB.

**1. ¿Tiene Docker?** Compruébalo:

```bash
docker compose version
```

Si responde con una versión, salta al paso 2. Si dice que no existe el comando:

```bash
sudo bash install-docker.sh
```

Instala Docker desde el archivo `docker-offline-*.tar.gz` de esta carpeta, sin
descargar nada. Necesita el paquete `iptables`, que Ubuntu y Debian traen de
serie. Si falta, el script lo dice y explica cómo ponerlo.

**2. Instalar el geocodificador:**

```bash
sudo bash install.sh
```

Pregunta el puerto (por defecto **8080**), carga la imagen y arranca el
servicio. El primer arranque tarda **un par de minutos**: carga las calles de
toda Cuba y prepara el grafo de rutas. El script espera y avisa cuando está
listo.

Para instalar sin preguntas: `sudo bash install.sh --port 8080`.

El servicio arranca solo cada vez que se enciende la máquina.

## Usar

Abre en el navegador **http://localhost:8080**. Desde otra máquina de la misma
red, usa la IP de esta: `http://<ip>:8080`.

Arriba hay cinco modos:

- **Punto.** Escribe una dirección y la marca en el mapa con su radio de
  precisión. Si haces clic en el mapa, te dice qué dirección hay en ese punto.
- **Región.** Escribe tres o más calles ("23, 21, L, M") y dibuja la manzana o
  zona que encierran.
- **Ruta.** Da el camino más rápido en coche entre un origen y un destino.
- **Viaje.** Haz clic en varias paradas (hasta 12) y calcula el orden de visita
  más corto.
- **Lote.** Geocodifica un Excel completo; se explica abajo.

### Geocodificar un Excel (modo Lote)

1. Pulsa **Lote** y elige un archivo `.xlsx` o `.csv`. Se lee en el propio
   navegador. Solo las direcciones viajan al servidor, que está en esta misma
   máquina.
2. Comprueba la columna de **Dirección** y, si la hay, la de **Municipio**. Se
   detectan solas por el nombre de la cabecera. Indicar el municipio mejora
   mucho el resultado cuando la dirección no lo trae.
3. Pulsa **Geocodificar**. Los puntos aparecen en el mapa a medida que llegan.
   El color indica la precisión: verde para un portal o una esquina, azul para
   un tramo "entre calles", naranja para un lugar por su nombre, amarillo para
   la calle, y rojo cuando solo se sabe el barrio o el municipio.
4. Haz clic en cualquier punto para ver su fila del Excel y lo que se encontró.
5. **Descargar .xlsx** devuelve tu Excel con seis columnas nuevas: `lat`,
   `lon`, `precision`, `radio_m` (el margen de error en metros), `resultado` y
   `municipio_resultado`. Las filas que no se encontraron aparecen también en la
   lista **sin resultado** del panel.

Como referencia, `ejemplo-negocios.xlsx` (5000 filas) tarda unos 30 segundos en
un portátil corriente. El servicio usa alrededor de 1.3 GB de RAM.

### La API

La documentación interactiva está en **http://localhost:8080/docs**. Lo más
usado:

```bash
# una dirección
curl "http://localhost:8080/api/v1/resolve?q=Calle%2023%20e/%20L%20y%20M,%20Vedado"

# hasta 100 direcciones de una vez
curl -X POST http://localhost:8080/api/v1/resolve/batch \
  -H 'content-type: application/json' \
  -d '{"queries": ["Hotel Nacional", "23 esq. a L, Vedado"]}'

# qué hay en un punto
curl "http://localhost:8080/api/v1/reverse?lat=23.1432&lon=-82.3807"
```

## Administrar

Todos estos comandos se ejecutan dentro de la carpeta donde se instaló.

| Qué | Comando |
|---|---|
| Ver si está vivo | `curl http://localhost:8080/health` |
| Ver los registros | `sudo docker compose logs -f` |
| Parar | `sudo docker compose down` |
| Arrancar | `sudo docker compose up -d` |
| Actualizar | Copia el `geocoder-docker.tar.gz` nuevo encima y vuelve a ejecutar `sudo bash install.sh`. |
| Desinstalar | `sudo docker compose down && sudo docker rmi $(sudo docker images -q geocoder)` |

El geocodificador no guarda datos propios. Actualizarlo o reinstalarlo no borra
nada.

## Si algo falla

- **"Falta docker" o "permission denied" al ejecutar `install.sh`.** Ejecútalo
  con `sudo`. Si acabas de instalar Docker y quieres usarlo sin `sudo`, cierra
  la sesión y vuelve a entrar.
- **El navegador no abre la página justo después de instalar.** Espera uno o
  dos minutos: el primer arranque prepara los índices. `sudo docker compose
  logs -f` muestra en qué va.
- **La primera ruta tarda.** El grafo de rutas termina de cargarse en segundo
  plano durante el primer minuto y medio. Después las rutas son inmediatas.
- **El puerto 8080 está ocupado.** Reinstala con otro:
  `sudo bash install.sh --port 8090`.
- **`install-docker.sh` dice que falta iptables.** Instálalo desde el medio de
  instalación de tu distribución (`sudo apt install iptables` en Ubuntu/Debian)
  y vuelve a ejecutarlo.
