
import os
import json
import logging
import hashlib

from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("ingesta_itaca_bd")


CAMPOS = {
    "alumnos": [
        "anyo", "nia", "sexo", "nacionalidad", "provincia",
        "municipio", "ensenanza", "curso", "grupo", "turno",
        "linea", "modalidad", "repite", "estado_matricula",
        "tipo_matricula", "fecha_matricula", "fecha_ingreso_centro",
    ],
    "calificaciones": [
        "anyo", "evaluacion", "alumno", "ensenanza", "curso",
        "contenido", "bloque_contenido", "nota_numerica", "tipo_nota",
    ],
    "contenidos": [
        "anyo", "codigo", "nombre_cas", "nombre_val",
        "ensenanza", "curso",
    ],
    "cursos": [
        "anyo", "codigo", "nombre_cas", "nombre_val",
        "abreviatura", "ensenanza", "padre",
    ],
    "grupos": [
        "anyo", "codigo", "nombre", "ensenanza", "turno",
        "aula", "capacidad", "linea", "modalidad",
    ],
}


RUTA_RAW = Path("data/raw")


def conectar():
    """Establece la conexión con PostgreSQL."""

    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )


def extraer_tabla(conexion, tabla: str) -> list[dict]:
    """Extrae los campos seleccionados de una tabla."""

    campos = CAMPOS[tabla]
    consulta = f"SELECT {', '.join(campos)} FROM {tabla}"

    with conexion.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(consulta)
        datos = [dict(fila) for fila in cur.fetchall()]

    logger.info(
        "Extraídos %d registros de la tabla %s",
        len(datos),
        tabla
    )

    return datos


def anonimizar_nia(nia: str) -> str:
    """Genera el hash SHA-256 truncado del NIA."""

    return hashlib.sha256(
        str(nia).encode()
    ).hexdigest()[:10]


def anonimizar_datos(datos_totales: dict) -> None:
    """Anonimiza el NIA manteniendo la correspondencia entre tablas."""

    mapa_nia = {}

    # Recopilar los NIA originales de ambas tablas.
    for fila in datos_totales["alumnos"]:
        nia = fila.get("nia")

        if nia is not None:
            mapa_nia[nia] = anonimizar_nia(nia)

    for fila in datos_totales["calificaciones"]:
        nia = fila.get("alumno")

        if nia is not None and nia not in mapa_nia:
            mapa_nia[nia] = anonimizar_nia(nia)

    # Sustituir los identificadores originales por sus hashes.
    for fila in datos_totales["alumnos"]:
        nia = fila.get("nia")

        if nia is not None:
            fila["nia"] = mapa_nia[nia]

    for fila in datos_totales["calificaciones"]:
        nia = fila.get("alumno")

        if nia is not None:
            fila["alumno"] = mapa_nia[nia]

    logger.info(
        "Anonimización completada para %d NIA distintos",
        len(mapa_nia)
    )


def diagnosticar_referencias(datos_totales: dict) -> None:
    """Comprueba referencias antes de anonimizar los identificadores."""

    alumnos = {
        (fila.get("anyo"), fila.get("nia"))
        for fila in datos_totales["alumnos"]
        if fila.get("nia") is not None
    }

    alumnos_normalizados = {
        (fila.get("anyo"), str(fila.get("nia")).strip())
        for fila in datos_totales["alumnos"]
        if fila.get("nia") is not None
    }

    identificadores_alumnos = {
        fila.get("nia")
        for fila in datos_totales["alumnos"]
        if fila.get("nia") is not None
    }

    errores = {
        "nulos": 0,
        "espacios": 0,
        "otro_anyo": 0,
        "no_existen": 0
    }

    identificadores_no_encontrados = set()

    for calificacion in datos_totales["calificaciones"]:
        anyo = calificacion.get("anyo")
        identificador = calificacion.get("alumno")
        clave = (anyo, identificador)

        if clave not in alumnos:
            if identificador is None:
                errores["nulos"] += 1

            elif (anyo, str(identificador).strip()) in alumnos_normalizados:
                errores["espacios"] += 1

            elif identificador in identificadores_alumnos:
                errores["otro_anyo"] += 1

            else:
                errores["no_existen"] += 1

            if identificador is not None:
                identificadores_no_encontrados.add(identificador)

    grupos_vacios = sum(
        1
        for alumno in datos_totales["alumnos"]
        if alumno.get("grupo") is not None
        and not str(alumno.get("grupo")).strip()
    )

    referencias_por_anyo = {}

    for calificacion in datos_totales["calificaciones"]:
        identificador = calificacion.get("alumno")
        anyo = calificacion.get("anyo")

        clave = (anyo, identificador)

        if clave not in alumnos:
            referencias_por_anyo[anyo] = (
                referencias_por_anyo.get(anyo, 0) + 1
            )

    logger.info(
        "Referencias no encontradas agrupadas por año: %s",
        referencias_por_anyo
    )

    logger.info("Diagnóstico de referencias de alumnos:")
    logger.info("Referencias nulas: %d", errores["nulos"])
    logger.info(
        "Referencias con diferencias de espacios: %d",
        errores["espacios"]
    )
    logger.info(
        "Referencias encontradas en otro año: %d",
        errores["otro_anyo"]
    )
    logger.info(
        "Referencias que no aparecen en alumnos: %d",
        errores["no_existen"]
    )
    logger.info(
        "Identificadores distintos no encontrados: %d",
        len(identificadores_no_encontrados)
    )
    logger.info(
        "Alumnos con grupo vacío o compuesto por espacios: %d",
        grupos_vacios
    )


def validar_claves(datos_totales: dict) -> tuple:
    """Valida las referencias entre alumnos, calificaciones, contenidos y grupos."""

    alumnos = {
        (fila["anyo"], fila["nia"])
        for fila in datos_totales["alumnos"]
        if fila.get("nia") is not None
    }

    contenidos = {
        (fila["anyo"], fila["codigo"])
        for fila in datos_totales["contenidos"]
        if fila.get("codigo") is not None
    }

    grupos = {
        (fila["anyo"], fila["codigo"])
        for fila in datos_totales["grupos"]
        if fila.get("codigo") is not None
    }

    muestras_alumnos = []
    muestras_grupos = []

    errores_alumnos = 0
    errores_contenidos = 0
    errores_grupos = 0

    for calificacion in datos_totales["calificaciones"]:
        clave_alumno = (
            calificacion.get("anyo"),
            calificacion.get("alumno")
        )

        clave_contenido = (
            calificacion.get("anyo"),
            calificacion.get("contenido")
        )

        if clave_alumno not in alumnos:
            errores_alumnos += 1

            if len(muestras_alumnos) < 10:
                muestras_alumnos.append(clave_alumno)

        if clave_contenido not in contenidos:
            errores_contenidos += 1

    for alumno in datos_totales["alumnos"]:
        grupo = alumno.get("grupo")

        if grupo is not None:
            clave_grupo = (
                alumno.get("anyo"),
                grupo
            )

            if clave_grupo not in grupos:
                errores_grupos += 1

                if len(muestras_grupos) < 10:
                    muestras_grupos.append(clave_grupo)

    logger.info(
        "Validación de claves completada: %d errores en alumnos, "
        "%d errores en contenidos, %d errores en grupos",
        errores_alumnos,
        errores_contenidos,
        errores_grupos
    )

    logger.info(
        "Ejemplos de alumnos no encontrados: %s",
        muestras_alumnos
    )

    logger.info(
        "Ejemplos de grupos no encontrados: %s",
        muestras_grupos
    )

    if (
        errores_alumnos == 0
        and errores_contenidos == 0
        and errores_grupos == 0
    ):
        logger.info("Todas las claves son válidas")
    else:
        logger.warning(
            "Se han encontrado referencias que no existen "
            "en las tablas correspondientes."
        )

    return errores_alumnos, errores_contenidos, errores_grupos


def guardar(datos: list[dict], nombre: str) -> None:
    """Guarda los registros de una tabla en formato JSON."""

    RUTA_RAW.mkdir(parents=True, exist_ok=True)

    fecha = date.today().isoformat()
    ruta = RUTA_RAW / f"{nombre}_{fecha}.json"

    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    logger.info(
        "Guardados %d registros en %s",
        len(datos),
        ruta
    )


def main():
    conexion = None

    try:
        logger.info("Iniciando proceso de ingesta ITACA")

        conexion = conectar()
        logger.info("Conexión con PostgreSQL establecida")

        datos_totales = {}

        for tabla in CAMPOS:
            datos_totales[tabla] = extraer_tabla(
                conexion,
                tabla
            )

        diagnosticar_referencias(datos_totales)

        anonimizar_datos(datos_totales)

        validar_claves(datos_totales)

        for tabla, datos in datos_totales.items():
            guardar(datos, tabla)

        logger.info("Proceso de ingesta finalizado correctamente")

    except (psycopg2.Error, OSError, ValueError) as error:
        logger.exception("Error durante la ingesta: %s", error)
        raise

    finally:
        if conexion is not None:
            conexion.close()
            logger.info("Conexión con PostgreSQL cerrada")


if __name__ == "__main__":
    main()
