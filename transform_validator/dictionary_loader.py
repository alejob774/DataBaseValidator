import pandas as pd

def load_dictionary(dict_path: str) -> dict[str, pd.DataFrame]:
    """
    Carga el diccionario (por ejemplo Base.xlsx) en un dict:
        { nombre_hoja_en_mayúsculas: DataFrame }

    Se asume que existen al menos las hojas:
        - PARAMETER
        - SEGMENT
        - <PAIS> (CHILE, PERU, ECUADOR, COLOMBIA, BOLIVIA, ...)
    """
    xls = pd.ExcelFile(dict_path, engine="openpyxl")
    dict_dfs: dict[str, pd.DataFrame] = {}

    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        df.columns = [str(c).strip() for c in df.columns]
        dict_dfs[sheet.strip().upper()] = df

    return dict_dfs