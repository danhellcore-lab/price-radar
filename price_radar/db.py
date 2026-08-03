"""Capa mínima para hablar con SQLite (local) o PostgreSQL (nube).

No se usa un ORM a propósito: las consultas son pocas y simples, y meter
SQLAlchemy solo por esto engordaría el .exe sin ganar nada. Lo único que
cambia de verdad entre los dos motores es el marcador de parámetros (`?` vs
`%s`) y el tipo de la clave autoincremental, así que se resuelve eso y ya.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


def is_postgres_url(url: str | None) -> bool:
    if not url:
        return False
    return urlparse(url).scheme in ("postgres", "postgresql", "postgresql+psycopg")


def clean_url(raw: str | None) -> str | None:
    """Limpia una cadena de conexión copiada a mano.

    Al pasar por el portapapeles, el Bloc de notas o la tubería de PowerShell,
    la cadena puede llegar con marca de orden de bytes (BOM), comillas o saltos
    de línea. Cualquiera de esos caracteres invisibles rompe el esquema de la
    URL y hacía que la app se fuera a SQLite en silencio, guardando los datos
    en un disco temporal que se borra.
    """
    if not raw:
        return None
    value = raw.strip().lstrip("﻿").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value or None


def resolve_url(explicit: str | None = None) -> str | None:
    """Cadena de conexión a la nube, si la hay.

    Prioridad: lo que se pase por código > variable de entorno (que es como la
    inyecta GitHub Actions) > nada, en cuyo caso se usa SQLite local.
    """
    return clean_url(explicit) or clean_url(os.environ.get("DATABASE_URL"))


class Database:
    """Envuelve una conexión y normaliza las diferencias entre motores."""

    def __init__(self, sqlite_path: Path | None = None, url: str | None = None):
        self.url = resolve_url(url)
        self.postgres = is_postgres_url(self.url)
        self.sqlite_path = sqlite_path
        if not self.postgres and sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    # -- dialecto --

    @property
    def ph(self) -> str:
        """Marcador de parámetros del motor actual."""
        return "%s" if self.postgres else "?"

    @property
    def pk(self) -> str:
        return "SERIAL PRIMARY KEY" if self.postgres else "INTEGER PRIMARY KEY"

    @property
    def real(self) -> str:
        """Tipo para los precios.

        En PostgreSQL `REAL` es de precisión simple (~7 dígitos significativos):
        un precio de 1.299.990 queda justo en el borde y podría redondearse.
        SQLite usa 8 bytes para REAL, así que ahí no hay problema.
        """
        return "DOUBLE PRECISION" if self.postgres else "REAL"

    def q(self, sql: str) -> str:
        """Traduce los `?` de una consulta al dialecto activo."""
        return sql.replace("?", "%s") if self.postgres else sql

    # -- conexión --

    def connect(self):
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, row_factory=dict_row, autocommit=False)

        conn = sqlite3.connect(self.sqlite_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    # -- helpers --

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(self.q(sql), tuple(params))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(self.q(sql), tuple(params))
            row = cur.fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.connect() as conn:
            conn.cursor().execute(self.q(sql), tuple(params))
            if self.postgres:
                conn.commit()

    def execute_many_statements(self, statements: Iterable[str]) -> None:
        with self.connect() as conn:
            cur = conn.cursor()
            for statement in statements:
                if statement.strip():
                    cur.execute(statement)
            if self.postgres:
                conn.commit()

    def describe(self) -> str:
        if not self.postgres:
            return f"SQLite local ({self.sqlite_path})"
        host = urlparse(self.url).hostname or "?"
        return f"PostgreSQL en la nube ({host})"
