"""
Excel/OneDrive Data Handler
Reads leads from active Excel workbook using win32com and shadow-copying
"""

from .active_data_reader import ActiveExcelReader
from typing import List, Dict, Any, Optional
import config


class DataHandler:
    """Handles reading and processing leads from active Excel workbook"""
    
    def __init__(self):
        self.reader = ActiveExcelReader()
        self.contacts = []  # List of processed contact dictionaries
        self.current_index = 0  # Track current position in contacts list
    
    def load_leads(self, sheet_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Load leads from active Excel workbook.
        Detects active workbook, shadow-copies to temp, and processes contacts.
        
        Args:
            sheet_name: Optional sheet name to read. If None, reads active sheet.
        
        Returns:
            List of standardized contact dictionaries
        """
        try:
            # Load data using shadow copy (bypasses file locks)
            df = self.reader.load_data_shadow_copy(sheet_name=sheet_name)
            
            # Process contacts with dynamic column handling
            # This automatically handles multiple phone/email columns as lists
            self.contacts = self.reader.process_contacts(df)
            self.current_index = 0
            
            return self.contacts
        except Exception as e:
            print(f"Error loading leads: {e}")
            return []
    
    def get_next_lead(self) -> Optional[Dict[str, Any]]:
        """
        Get the next lead to call.
        
        Returns:
            Contact dictionary with standardized keys, or None if no more leads
        """
        if not self.contacts or self.current_index >= len(self.contacts):
            return None
        
        lead = self.contacts[self.current_index]
        return lead
    
    def mark_lead_processed(self):
        """Mark current lead as processed and move to next"""
        if self.current_index < len(self.contacts):
            self.current_index += 1
    
    def get_active_file_path(self) -> str:
        """
        Get the path of the currently active Excel workbook.
        
        Returns:
            Full path to the active workbook
        """
        return self.reader.get_active_file_path()
    
    def reload_leads(self, sheet_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Reload leads from active workbook (useful when data changes).
        
        Args:
            sheet_name: Optional sheet name to read. If None, reads active sheet.
        
        Returns:
            List of standardized contact dictionaries
        """
        return self.load_leads(sheet_name=sheet_name)

