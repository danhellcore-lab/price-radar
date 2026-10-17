---
name: orquestar
description: Antes de ejecutar una tarea, lanza sub-agentes que (1) optimizan el prompt con prompt-master, (2) eligen el modelo mas barato que sirva (haiku/sonnet/opus) y (3) ejecutan con ese modelo, para no gastar tokens. Usar cuando el usuario diga "/orquestar <tarea>", "elige el mejor modelo y ejecuta" o pida ahorrar tokens en una tarea.
---

# Orquestar: prompt-master + seleccion de modelo + ejecucion

Entrada: la tarea del usuario (`$ARGUMENTS`). Si esta vacia, pidela.

## Paso 1: Sub-agente "preparador" (siempre `model: haiku`)

Lanza UN solo sub-agente con la herramienta Agent (`subagent_type: general-purpose`, `model: haiku`) con este encargo:

> Tarea original: <tarea>.
> 1. Invoca la skill `prompt-master` (si existe) y reescribe la tarea como un prompt optimizado: objetivo, contexto del repo necesario, restricciones, formato de salida, criterio de "terminado". Sin relleno.
> 2. Clasifica la tarea y devuelve SOLO este JSON:
>    `{"prompt": "<prompt optimizado>", "modelo": "haiku|sonnet|opus", "razon": "<1 linea>", "paralelizable": true|false}`

Si `prompt-master` no esta disponible, el preparador aplica el mismo criterio por su cuenta.

## Criterio de modelo (el preparador debe aplicarlo)

| Modelo | Cuando |
|---|---|
| **haiku** | Buscar/listar/resumir archivos, renombres, formato, edicion mecanica, preguntas con respuesta localizable, tareas de 1-2 archivos sin ambiguedad |
| **sonnet** | Por defecto. Implementar features, arreglar bugs con causa clara, tests, refactors acotados, scrapers/parsers |
| **opus** | Diseno de arquitectura, bugs sin causa conocida tras un intento fallido, cambios que cruzan muchos modulos, seguridad, razonamiento largo o ambiguo |

Regla de desempate: elegir el modelo mas barato que cumpla; subir de nivel solo si hay ambiguedad real o riesgo alto. Nunca opus "por si acaso".

## Paso 2: Ejecutor

Con el JSON devuelto, lanza el sub-agente ejecutor (`subagent_type: general-purpose`, `model: <modelo>`) con `prompt` = el prompt optimizado. Si `paralelizable` es true y la tarea se divide en partes independientes, lanza varios ejecutores en el mismo turno.

## Paso 3: Reporte

Responde al usuario con:
- Modelo elegido y razon (1 linea).
- Resultado del ejecutor, resumido.
- Si el ejecutor fallo o dijo que le queda grande, relanzar UNA vez con el siguiente modelo superior e indicarlo.

## Reglas para ahorrar tokens

- No ejecutes la tarea tu mismo en el hilo principal: delega.
- El preparador siempre es haiku; su salida es solo el JSON, nada mas.
- No pases al ejecutor el historial de la conversacion; solo el prompt optimizado.
- Tareas triviales (una lectura, una edicion de una linea): salta el Paso 1 y hazlas directo.
