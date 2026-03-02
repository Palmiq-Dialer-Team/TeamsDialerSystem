"""Read from the active Excel workbook via COM."""

import re
import win32com.client
import pandas as pd
import shutil
import tempfile
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple


class ActiveExcelReader:
    """Reads from the running Excel instance."""
    
    def __init__(self):
        self.excel_app = None
        self.active_workbook = None
        self.active_sheet_name = None
    
    def get_active_file_path(self) -> str:
        """Get the active workbook's full path. Retries on COM latency."""
        import time
        
        max_retries = 5
        for i in range(max_retries):
            try:
                self.excel_app = win32com.client.GetActiveObject("Excel.Application")
                self.active_workbook = self.excel_app.ActiveWorkbook
                
                if self.active_workbook is None:
                    if i < max_retries - 1:
                        time.sleep(1)
                        continue
                    raise RuntimeError("No active workbook found in Excel. Please open an Excel file first.")
                
                full_path = self.active_workbook.FullName
                
                if not full_path:
                    if i < max_retries - 1:
                        time.sleep(1)
                        continue
                    raise RuntimeError("Active workbook does not have a saved path. Please save the workbook first.")
                
                return full_path
            
            except Exception as e:
                if "GetActiveObject" in str(type(e).__name__):
                    if i < max_retries - 1:
                        time.sleep(1)
                        continue
                    raise RuntimeError("No Excel instance is currently running. Please open Excel first.") from e
                
                if i < max_retries - 1:
                    time.sleep(1)
                    continue
                
                raise RuntimeError(f"Error getting active file path: {e}") from e
        
        raise RuntimeError("Could not connect to Excel after multiple attempts.")
    
    def load_data_shadow_copy(self, sheet_name: Optional[str] = None, visible_only: bool = False) -> Tuple[pd.DataFrame, int]:
        """Load data, auto-detect header row. Skips bad sheets. visible_only=filtered rows."""
        try:
            self.get_active_file_path()
            if not self.active_workbook:
                raise RuntimeError("No active workbook.")

            all_data_frames = []
            skipped_sheets = 0
            skipped_rows = 0
            
            if sheet_name:
                sheets_to_read = [self.active_workbook.Worksheets(sheet_name)]
            else:
                try:
                    sheets_to_read = [self.active_workbook.Worksheets(i) for i in range(1, self.active_workbook.Worksheets.Count + 1)]
                except Exception:
                    sheets_to_read = [self.active_workbook.ActiveSheet]

            for sheet in sheets_to_read:
                try:
                    sheet_name_val = getattr(sheet, "Name", "Sheet")
                    rng = sheet.UsedRange
                    raw_data = rng.Value  # Tuple of tuples

                    if not raw_data or len(raw_data) < 1:
                        continue

                    data_list = list(raw_data)
                    
                    header_row_idx = 0
                    found_header = False
                    keywords = ['email', 'phone', 'first name', 'company', 'state']
                    
                    for i, row in enumerate(data_list[:10]):
                        if row is None:
                            continue
                        row_iter = row if hasattr(row, '__iter__') and not isinstance(row, str) else [row]
                        row_str = [str(x).lower() for x in row_iter if x is not None]
                        matches = sum(1 for k in keywords if any(k in str(cell) for cell in row_str))
                        if matches >= 2:
                            header_row_idx = i
                            found_header = True
                            break
                    
                    if not found_header:
                        skipped_sheets += 1
                        continue
                    
                    if len(data_list) <= header_row_idx + 1:
                        continue

                    headers_raw = data_list[header_row_idx]
                    rows_raw = data_list[header_row_idx + 1:]
                    headers = [str(h).strip() if h is not None else f"Unnamed_{i}" for i, h in enumerate(headers_raw)]
                    seen = {}
                    for i, h in enumerate(headers):
                        if h in seen:
                            seen[h] += 1
                            headers[i] = f"{h}_{seen[h]}"
                        else:
                            seen[h] = 0
                    if visible_only:
                        xlCellTypeVisible = 12
                        try:
                            vis_rng = rng.SpecialCells(xlCellTypeVisible)
                            visible_rows = set()
                            for area in (vis_rng.Areas if hasattr(vis_rng, 'Areas') else [vis_rng]):
                                for r in range(area.Row, area.Row + area.Rows.Count):
                                    visible_rows.add(r)
                            first_data_row = header_row_idx + 2  # 1-based Excel row
                            rows_raw = [r for i, r in enumerate(rows_raw) if (first_data_row + i) in visible_rows]
                        except Exception:
                            pass
                    n_cols = len(headers)
                    rows = []
                    for r in rows_raw:
                        if r is None:
                            continue
                        r_list = list(r) if hasattr(r, '__iter__') and not isinstance(r, str) else [r]
                        if len(r_list) > n_cols:
                            r_list = r_list[:n_cols]
                        elif len(r_list) < n_cols:
                            r_list = r_list + [None] * (n_cols - len(r_list))
                        rows.append(r_list)
                    
                    df = pd.DataFrame(rows, columns=headers)
                    
                    df["Sheet Name"] = sheet_name_val
                    
                    df["_header_offset"] = header_row_idx + 1 
                    
                    df["_sheet_relative_idx"] = range(len(df))
                    
                    all_data_frames.append(df)
                        
                except Exception as e:
                    print(f"Error reading sheet {sheet.Name}: {e}")

            if not all_data_frames:
                return pd.DataFrame(), 1

            full_df = pd.concat(all_data_frames, ignore_index=True)
            return full_df, 1 # Return 1 as default ref

        except Exception as e:
            raise RuntimeError(f"Error loading data: {e}") from e

    def activate_sheet(self, sheet_name: Optional[str]) -> None:
        """Activate a sheet in the workbook."""
        if not sheet_name:
            return
        try:
            if not self.active_workbook:
                self.get_active_file_path()
            if not self.active_workbook:
                return
            sheet = self.active_workbook.Worksheets(sheet_name)
            sheet.Activate()
            self.active_sheet_name = str(sheet_name)
        except Exception as e:
            print(f"Error activating sheet '{sheet_name}': {e}")
    
    def process_contacts(self, df: pd.DataFrame, sheet_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Process DataFrame into list of contact dictionaries with smart mapping.
        """
        contacts = []

        if sheet_name is None:
            if self.active_sheet_name:
                sheet_name = self.active_sheet_name
            elif self.active_workbook:
                try:
                    active_sheet = self.active_workbook.ActiveSheet
                    sheet_name = str(active_sheet.Name).strip()
                    self.active_sheet_name = sheet_name
                except Exception as e:
                    print(f"Warning: Could not get sheet name from workbook: {e}")
                    sheet_name = "Sheet1"
            else:
                sheet_name = "Sheet1"
        
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            sheet_name = "Sheet1"

        df.columns = [str(col).strip() for col in df.columns]

        mappings = {
            'First Name': ['first name'],
            'Last Name': ['last name'],
            'Title': ['title', 'job title'],
            'Company': ['company', 'company name'],
            'Website': ['website', 'company website', 'company website domain'],
            'Industry': ['industry', 'company industry'],
            'Revenue': ['revenue', 'company revenue range', 'annual revenue'],
            'State': ['state', 'company state abbr'],
            'Notes': ['notes']
        }

        # Optional per-row metadata columns
        dispo_col = next((c for c in df.columns if 'disposition stamp' in str(c).lower()), None)
        # Exact header for LinkedIn profile URL
        linkedin_col = next(
            (c for c in df.columns if str(c).strip().lower() == "contact li profile url"),
            None,
        )

        for idx, row in df.iterrows():
            try:
                contact = {}

                for target, potential_headers in mappings.items():
                    for header in potential_headers:
                        match = next((c for c in df.columns if str(c).lower() == header), None)
                        if match and pd.notna(row[match]):
                            contact[target] = str(row[match]).strip()
                            break
                    if target not in contact:
                        contact[target] = "-"

                emails = []
                for col in df.columns:
                    if 'email' in str(col).lower():
                        val = row[col]
                        if pd.notna(val) and str(val).strip():
                            emails.append(str(val).strip())
                contact['Emails'] = emails

                # LinkedIn profile URL (exact header: "Contact LI Profile URL")
                if linkedin_col:
                    val = row[linkedin_col]
                    if pd.notna(val):
                        url = str(val).strip()
                        if url:
                            contact["LinkedIn"] = url

                phones = []
                for col in df.columns:
                    c_lower = str(col).lower()
                    phone_type = "Other"
                    if 'mobile' in c_lower or 'cell' in c_lower:
                        phone_type = "Mobile"
                    elif 'direct' in c_lower:
                        phone_type = "Direct"
                    elif 'company' in c_lower or 'business' in c_lower or 'office' in c_lower:
                        phone_type = "Company"
                    elif 'phone' in c_lower:
                        phone_type = "Direct"
                    if 'phone' in c_lower or 'mobile' in c_lower or 'cell' in c_lower:
                        val = row[col]
                        if pd.notna(val):
                            clean_num = str(val).strip()
                            if clean_num and not any(p['number'] == clean_num for p in phones):
                                phones.append({'number': clean_num, 'type': phone_type, 'original_col': col})
                contact['Phone_Objects'] = phones
                contact['Phones'] = [p['number'] for p in phones]

                row_sheet_name = str(row.get("Sheet Name", sheet_name)).strip()
                sheet_timezone = "-"
                if row_sheet_name:
                    match = re.search(r'\((ET|CT|MT|PT)\)', row_sheet_name, re.IGNORECASE)
                    if match:
                        sheet_timezone = match.group(1).upper()
                contact["Timezone"] = sheet_timezone

                current_dispo = None
                if dispo_col:
                    val = row[dispo_col]
                    if pd.notna(val):
                        try:
                            import json
                            data = json.loads(str(val))
                            current_dispo = data.get("disposition")
                        except Exception:
                            pass
                contact['current_disposition'] = current_dispo

                sheet_relative_idx = row.get("_sheet_relative_idx", idx)
                header_offset = row.get("_header_offset", 1)
                excel_row_number = int(header_offset) + 1 + int(sheet_relative_idx)
                contact["rowNumber"] = excel_row_number
                contact["Original Row Index"] = excel_row_number
                contact["Sheet Name"] = row.get("Sheet Name", sheet_name)
                contact["sheetName"] = row.get("Sheet Name", sheet_name)

                if contact.get('First Name') != '-' or contact.get('Company') != '-':
                    contacts.append(contact)
            except Exception:
                continue  # Skip row if we can't parse it

        return contacts
