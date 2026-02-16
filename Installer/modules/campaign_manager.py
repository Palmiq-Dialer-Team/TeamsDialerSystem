class CampaignManager:
    def __init__(self):
        # Modes: 'FRESH', 'RECYCLE', 'TARGETED', 'ALL'
        self.mode = "FRESH"
        self.target_disposition = None  # Only used for TARGETED mode

    def set_mode(self, mode, target=None):
        self.mode = mode
        self.target_disposition = target

    def filter_indices_by_campaign(self, contact_list):
        """
        Returns a list of indices that match the current campaign mode.
        This works in ADDITION to the FilterManager.
        """
        valid_indices = []

        for i, contact in enumerate(contact_list):
            dispo = contact.get("current_disposition")

            if self.mode == "FRESH":
                if not dispo:  # None or Empty
                    valid_indices.append(i)

            elif self.mode == "RECYCLE":
                if dispo == "No Response":
                    valid_indices.append(i)

            elif self.mode == "TARGETED":
                if dispo == self.target_disposition:
                    valid_indices.append(i)

            elif self.mode == "ALL":
                valid_indices.append(i)

        return valid_indices

    def analyze_buckets(self, contact_list):
        """Returns counts for each bucket."""
        stats = {
            "FRESH": 0,
            "RECYCLE": 0,  # No Response
            "OTHER": {},
        }

        for contact in contact_list:
            dispo = contact.get("current_disposition")
            if not dispo:
                stats["FRESH"] += 1
            elif dispo == "No Response":
                stats["RECYCLE"] += 1
            else:
                stats["OTHER"][dispo] = stats["OTHER"].get(dispo, 0) + 1

        return stats


