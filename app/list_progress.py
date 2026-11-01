"""Bounded progress for an endlessly refreshed recommendation list."""


class ListProgress:
    def __init__(self, patience=3):
        self.patience = patience
        self.keys = set()
        self.idle = self.loading = 0

    def observe(self, keys, *, loading=False):
        fresh = set(keys) - self.keys
        self.keys.update(keys)
        if loading:
            self.loading += 1
            return "loading_timeout" if self.loading >= self.patience else None
        self.loading = 0
        self.idle = 0 if fresh else self.idle + 1
        if self.idle >= self.patience:
            return "no_new_cards" if self.keys else "unreadable_list"
        return None
