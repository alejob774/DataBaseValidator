import re
import unicodedata

import pandas as pd

class Normalizer:
    """
    Utilidad para normalizar textos de forma consistente:
      - Mayúsculas
      - Eliminar acentos
      - Eliminar signos raros y normalizar espacios
    """

    @staticmethod
    def standardize_text(value) -> str:
        if pd.isna(value):
            return ""

        text = str(value).strip().upper()

        # Eliminar acentos
        text = "".join(
            c
            for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )

        # Sustituir todo lo que no sea A-Z, 0-9 por espacio
        text = re.sub(r"[^A-Z0-9]+", " ", text)

        # Colapsar espacios
        text = re.sub(r"\s+", " ", text).strip()

        return text

    @staticmethod
    def parse_date_like_column(col_name):
        """
        Reproduce la lógica del mapper original:
         1) Intentar interpretar el nombre directamente como fecha.
         2) Si falla, buscar un patrón tipo 'ene-22', 'mar/23', 'aug 24', etc.
        Devuelve un Timestamp o None si no parece fecha.
        """
        dt = pd.to_datetime(col_name, errors="coerce")
        if pd.notnull(dt):
            return dt

        col_str = str(col_name).lower().strip()

        meses = r"(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|jan|apr|aug|dec)"
        pattern = re.compile(rf"{meses}[-/\s]?(\d{{2,4}})")
        match = pattern.search(col_str)
        if match:
            replacements = {"ene": "jan", "abr": "apr", "ago": "aug", "dic": "dec"}
            for sp, en in replacements.items():
                col_str = col_str.replace(sp, en)
            return pd.to_datetime(col_str, errors="coerce", format="%b-%y")

        return None