from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from .config import MappingConfig
from .normalizer import Normalizer

def build_lookups(
    dictionary_dfs: dict[str, pd.DataFrame],
    country: str,
    norm: Normalizer,
) -> tuple[Dict[str, str], Dict[Tuple[str, str], Dict[str, str]], Dict[str, str]]:
    """
    Construye:
      - param_lookup:  parametro_original_normalizado -> PARAMETER (normalizado)
      - country_lookup: (NAMEPLATE COUNTRY normalizado, TRIM alias normalizado) -> {NAMEPLATE, TRIM, CONCAT}
      - segmento_lookup: NAMEPLATE -> SEGMENT
    """
    country_name = country.upper()

    if "PARAMETER" not in dictionary_dfs or "SEGMENT" not in dictionary_dfs:
        raise ValueError("El diccionario debe contener las hojas 'PARAMETER' y 'SEGMENT'.")

    if country_name not in dictionary_dfs:
        raise ValueError(f"No existe la hoja '{country_name}' en el diccionario.")

    df_country = dictionary_dfs[country_name]
    df_params = dictionary_dfs["PARAMETER"]
    df_segmento = dictionary_dfs["SEGMENT"]

    # 1) Lookup de parámetros
    param_col_in_dict = next(
        (c for c in df_params.columns if str(c).strip().upper() == country_name),
        None,
    )
    if not param_col_in_dict:
        raise ValueError(
            f"No se encontró la columna de parámetros para el país '{country_name}' "
            f"en la hoja 'PARAMETER'."
        )

    param_lookup: Dict[str, str] = {
        norm.standardize_text(row[param_col_in_dict]): str(row["PARAMETER"]).upper()
        for _, row in df_params.iterrows()
        if pd.notna(row.get(param_col_in_dict))
    }

    # 2) Lookup país + TRIM alias -> NAMEPLATE/TRIM/CONCAT canónicos
    country_lookup: Dict[Tuple[str, str], Dict[str, str]] = {}

    for _, row in df_country.iterrows():
        npc_raw = row.get("NAMEPLATE COUNTRY")
        if pd.isna(npc_raw):
            continue

        npc_norm = norm.standardize_text(npc_raw)
        val_data = {
            "NAMEPLATE": str(row.get("NAMEPLATE", "")).upper(),
            "TRIM": str(row.get("TRIM", "")).upper(),
            "CONCAT": str(row.get("CONCAT", "")).upper(),
        }

        for col in ["TRIM 1", "TRIM 2", "TRIM 3"]:
            if col in df_country.columns and pd.notna(row.get(col)):
                trim_norm = norm.standardize_text(row[col])
                key = (npc_norm, trim_norm)
                if key not in country_lookup:
                    country_lookup[key] = val_data

    # 3) Lookup de segmento por NAMEPLATE
    if "NAMEPLATE" not in df_segmento.columns or "SEGMENT" not in df_segmento.columns:
        raise ValueError("La hoja 'SEGMENT' debe tener columnas 'NAMEPLATE' y 'SEGMENT'.")

    segmento_lookup: Dict[str, str] = (
        df_segmento.dropna(subset=["NAMEPLATE"])
        .set_index("NAMEPLATE")["SEGMENT"]
        .to_dict()
    )

    return param_lookup, country_lookup, segmento_lookup

def build_date_map(
    source_df: pd.DataFrame,
    mapping: MappingConfig,
    norm: Normalizer,
) -> Dict[Any, str]:
    """
    Determina qué columnas del origen representan fechas y cómo se llaman en la salida
    (por ejemplo '2022-01-01' -> 'JAN-22').
    """
    mapped_keys = {
        mapping.nameplate_column,
        mapping.trim_column,
        mapping.parameter_column,
    }

    date_map: Dict[Any, str] = {}

    for col in source_df.columns:
        if col in mapped_keys:
            continue
        dt = norm.parse_date_like_column(col)
        if dt is not None and pd.notnull(dt):
            formatted = dt.strftime("%b-%y").upper()
            date_map[col] = formatted

    return date_map

def map_source_to_normalized(
    source_df: pd.DataFrame,
    country: str,
    mapping: MappingConfig,
    dictionary_dfs: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Mapea fila a fila la base original al formato normalizado (sin agrupar).

    Devuelve:
      - DataFrame con filas mapeadas (incluyendo columna _SRC_INDEX para trazabilidad).
      - DataFrame con filas no mapeadas (para reporte).
    """
    norm = Normalizer()
    country_upper = country.upper()

    required_cols = [
        mapping.nameplate_column,
        mapping.trim_column,
        mapping.parameter_column,
    ]
    missing = [c for c in required_cols if c not in source_df.columns]
    if missing:
        raise KeyError(
            f"No se encontraron las columnas requeridas en la base original: {missing}. "
            f"Columnas disponibles: {list(source_df.columns)}"
        )

    param_lookup, country_lookup, segmento_lookup = build_lookups(
        dictionary_dfs=dictionary_dfs,
        country=country_upper,
        norm=norm,
    )

    date_map = build_date_map(source_df, mapping, norm)

    name_col = mapping.nameplate_column
    param_col = mapping.parameter_column

    valid_mask = (
        source_df[name_col].notna()
        & source_df[param_col].notna()
    )
    source_valid = source_df[valid_mask].copy()

    mapped_rows: List[Dict[str, Any]] = []
    unmapped_info: List[Dict[str, Any]] = []

    for idx, row in source_valid.iterrows():
        raw_npc = row.get(mapping.nameplate_column)
        raw_trim = row.get(mapping.trim_column)
        raw_param = row.get(mapping.parameter_column)

        if pd.isna(raw_npc) or pd.isna(raw_param):
            continue
        if any(token in str(raw_npc).strip().upper() for token in ("TOTAL", "ALL")):
            continue

        norm_npc = norm.standardize_text(raw_npc)
        norm_trim = norm.standardize_text(raw_trim)
        norm_param = norm.standardize_text(raw_param)

        if norm_param not in param_lookup:
            unmapped_info.append(
                {
                    "ROW_INDEX": idx,
                    "REASON": "UNKNOWN_PARAMETER",
                    "NAMEPLATE_ORIG": raw_npc,
                    "TRIM_ORIG": raw_trim,
                    "PARAM_ORIG": raw_param,
                }
            )
            continue

        base_data = country_lookup.get((norm_npc, norm_trim))
        if not base_data:
            unmapped_info.append(
                {
                    "ROW_INDEX": idx,
                    "REASON": "NO_DICTIONARY_MATCH",
                    "NAMEPLATE_ORIG": raw_npc,
                    "TRIM_ORIG": raw_trim,
                    "PARAM_ORIG": raw_param,
                }
            )
            continue

        new_row: Dict[str, Any] = {
            "COUNTRY": country_upper,
            "CHANNEL": "ALL CHANNELS",
            "NAMEPLATE": base_data["NAMEPLATE"],
            "TRIM": base_data["TRIM"],
            "CONCAT": base_data["CONCAT"],
            "SEGMENT": str(segmento_lookup.get(base_data["NAMEPLATE"], "OTROS")).upper(),
            "PARAMETER": param_lookup[norm_param],
            "_SRC_INDEX": idx,
        }

        for orig_col, formatted_col in date_map.items():
            val = row.get(orig_col)
            try:
                new_row[formatted_col] = float(val) if pd.notna(val) else 0.0
            except Exception:
                new_row[formatted_col] = 0.0

        mapped_rows.append(new_row)

    mapped_df = pd.DataFrame(mapped_rows)

    if mapped_df.empty:
        unmapped_df = pd.DataFrame(unmapped_info)
        return mapped_df, unmapped_df

    if mapped_df["_SRC_INDEX"].duplicated().any():
        raise ValueError(
            "Se encontraron filas de origen mapeadas más de una vez (_SRC_INDEX duplicado)."
        )

    unmapped_df = pd.DataFrame(unmapped_info)

    return mapped_df, unmapped_df