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
    """Establece la conexión con PostgreSQL"""
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def extraer_tabla(conexion, tabla:str) -> list[dict]:
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

    # Recopilar los NIA originales de ambas tablas
    for fila in datos_totales["alumnos"]:
        nia = fila.get("nia")
        if nia is not None:
            mapa_nia[nia] = anonimizar_nia(nia)

    for fila in datos_totales["calificaciones"]:
        nia = fila.get("alumno")
        if nia is not None:
            if nia not in mapa_nia:
                mapa_nia[nia] = anonimizar_nia(nia)

    # Sustituir los identificadores originales por sus hashes
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
        len(mapa_nia),
    )

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

        anonimizar_datos(datos_totales)

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