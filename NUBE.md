# Funcionar en la nube (sin tu computador encendido)

Al terminar esto, Price Radar buscará precios cada 30 minutos aunque tengas el
PC apagado, te avisará por Telegram y publicará una web que puedes abrir desde
el celular. **Todo en capas gratuitas.**

Necesitas crear dos cuentas (yo no puedo registrarte): **GitHub** y **Neon**.
Tiempo estimado: 20 minutos.

---

## 1. Base de datos en Neon (5 min)

1. Entra a <https://neon.com> y crea una cuenta (puedes usar tu cuenta de Google).
2. *Create project* → nombre `price-radar` → región **AWS us-east-1** (o la que
   te ofrezca por defecto).
3. Al terminar te muestra una **connection string**. Cópiala entera. Se ve así:

   ```
   postgresql://usuario:contraseña@ep-algo-123456.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```

   Guárdala en un bloc de notas: la necesitarás dos veces.

> Si cierras la ventana sin copiarla: panel del proyecto → *Connect* →
> *Connection string*.

---

## 2. Subir el proyecto a GitHub (5 min)

1. Crea una cuenta en <https://github.com> si no tienes.
2. Crea un repositorio nuevo: botón **+** → *New repository*.
   - Nombre: `price-radar`
   - **Público** (necesario para que GitHub Pages y los minutos de Actions sean
     gratis).
   - No marques nada más → *Create repository*.
3. Sube la carpeta del proyecto. Desde `price-radar/`:

   ```bash
   git init && git add . && git commit -m "Price Radar" && git branch -M main
   ```

   ```bash
   git remote add origin https://github.com/TU-USUARIO/price-radar.git && git push -u origin main
   ```

   Sustituye `TU-USUARIO` por tu nombre de usuario de GitHub.

> El archivo `.gitignore` ya excluye la base local, el `.venv` y el `.exe`, así
> que no subes nada pesado ni privado.

---

## 3. Configurar los secretos (5 min)

En tu repositorio en GitHub: **Settings** → **Secrets and variables** →
**Actions**.

En la pestaña **Secrets**, botón *New repository secret*, crea:

| Nombre | Valor |
|--------|-------|
| `DATABASE_URL` | La connection string de Neon del paso 1 |
| `TELEGRAM_BOT_TOKEN` | El token de @BotFather (opcional pero recomendado) |
| `TELEGRAM_CHAT_ID` | Tu chat id (lo ves en la app de escritorio, pestaña Ajustes) |

En la pestaña **Variables**, botón *New repository variable*:

| Nombre | Valor |
|--------|-------|
| `CATEGORIES` | `notebook,celular,televisor,zapatillas` |

`CATEGORIES` solo se usa la **primera vez**, para arrancar con algo. Después las
categorías se gestionan desde la app de escritorio y no se reponen las que borres.

---

## 4. Activar la web (2 min)

**Settings** → **Pages** → en *Source* elige **GitHub Actions**.

---

## 5. Encenderlo (1 min)

Pestaña **Actions** → *Buscar precios* → botón **Run workflow**.

La primera ejecución tarda unos 3 minutos (construye el índice de categorías de
Ripley). Cuando termine en verde, tu web estará en:

```
https://TU-USUARIO.github.io/price-radar/
```

A partir de ahí corre solo cada 30 minutos.

---

## 6. Conectar la app de escritorio (2 min)

Abre `PriceRadar.exe` → pestaña **Ajustes** → abajo del todo:

- **Conexión Neon**: pega la misma connection string.
- **URL del informe web**: `https://TU-USUARIO.github.io/price-radar/`

*Guardar ajustes* y reinicia la app. Verás **☁ conectado a la nube** arriba: ya
no busca nada por su cuenta, solo muestra lo que encontró la nube. Las
categorías que añadas ahí se buscarán en la siguiente ejecución.

---

## Preguntas frecuentes

**¿Cuánto cuesta?** Nada. GitHub Actions da 2.000 minutos/mes gratis en repos
públicos y cada ejecución usa ~1 minuto (unas 1.440 al mes con cron de 30 min —
si te acercas al límite, sube el intervalo a 60 min en `.github/workflows/scan.yml`).
La capa gratis de Neon incluye 0,5 GB, muy por encima de lo que ocupa esto.

**El cron no dispara a la hora exacta.** GitHub encola los cron programados
cuando hay carga; pueden pasar 5-15 minutos de más. Para vigilar precios da igual.

**¿Se puede parar?** Actions → *Buscar precios* → menú `...` → *Disable workflow*.

**¿Y si Neon suspende la base por inactividad?** La capa gratis duerme tras unos
minutos sin uso y despierta sola en la siguiente conexión; solo añade un par de
segundos al primer acceso.

**Cuidado con la connection string:** da acceso total a tu base. Va en *Secrets*
de GitHub (que están cifrados y no se ven en los logs), nunca dentro de un
archivo del repositorio.
