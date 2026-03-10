from dataclasses import dataclass
from typing import Optional

@dataclass
class AppConfig:
    dict_path: str
    original_path: str
    original_sheet: str
    output_path: str
    country: str
    # None  -> detección automática de la fila de cabecera
    # int   -> usar esa fila (0-based) como cabecera
    header_row: Optional[int]
    # Nombres de columnas en la base original (None => auto-detección)
    nameplate_column: Optional[str]
    trim_column: Optional[str]
    parameter_column: Optional[str]

@dataclass
class MappingConfig:
    """
    Define qué columnas de la base original se usan para:
      - NAMEPLATE COUNTRY (diccionario)
      - TRIM (TRIM 1/2/3 del diccionario)
      - Parámetro (columna del diccionario PARAMETER específica del país)
    """
    nameplate_column: str
    trim_column: str
    parameter_column: str
