"""Bounded list progress; lack of new cards is not proof of the list end."""


class ListProgress:
    def __init__(self, patience=3):
        self.patience = patience
        self.keys = set()
        self.idle = self.ends = self.loading = 0

    def observe(self, keys, *, end=False, loading=False):
        fresh = set(keys) - self.keys
        self.keys.update(keys)
        if loading:
            self.loading += 1
            self.ends = 0
            return "loading_timeout" if self.loading >= self.patience else None
        self.loading = 0
        self.idle = 0 if fresh else self.idle + 1
        self.ends = self.ends + 1 if end else 0
        if self.ends >= 2 and not fresh:
            return "list_end_confirmed"
        if self.idle >= self.patience:
            return "no_new_cards" if self.keys else "unreadable_list"
        return None
