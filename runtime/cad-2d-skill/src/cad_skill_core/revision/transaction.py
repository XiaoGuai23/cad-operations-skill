"""In-memory rollback for ezdxf edits, including renderer-created resources.

Rehearsal alone cannot protect against a second, caller-only renderer failure.
This checkpoint restores the complete document object graph and retains the
existing Python entities, DXF namespaces, layouts, tables and containers.
It is not a filesystem transaction and assumes exclusive access to the drawing.
"""
from copy import deepcopy


class DocumentCheckpoint:
    def __init__(self, doc):
        originals = {}

        def visit(value):
            identity = id(value)
            if identity in originals:
                return
            if isinstance(value, dict):
                originals[identity] = value
                for key, item in value.items():
                    visit(key)
                    visit(item)
            elif isinstance(value, (list, set)):
                originals[identity] = value
                for item in value:
                    visit(item)
            elif isinstance(value, tuple):
                # Tuples themselves are immutable, but can contain state.
                for item in value:
                    visit(item)
            elif type(value).__module__.startswith('ezdxf.') and hasattr(value, '__dict__'):
                originals[identity] = value
                visit(vars(value))

        visit(doc)
        copied = {}
        self.snapshot = deepcopy(doc, copied)
        pairs = {key: (obj, copied[key]) for key, obj in originals.items()
                 if key in copied and copied[key] is not obj}
        self.namespaces = [(obj, vars(obj)) for obj, _ in pairs.values()
                           if not isinstance(obj, (dict, list, set))]
        # DXFNamespace implements custom deepcopy and does not register its
        # attribute dictionary in deepcopy's memo. Pair those dictionaries
        # explicitly, including attributes absent before the failed operation.
        for obj, snapshot in list(pairs.values()):
            if not isinstance(obj, (dict, list, set)):
                pairs[id(vars(obj))] = (vars(obj), vars(snapshot))
        self.pairs = list(pairs.values())

    def restore(self):
        # Resolve cycles and every pre-existing object back to its original
        # identity; only newly introduced values need to be copied.
        memo = {id(snapshot): original for original, snapshot in self.pairs}
        for original, namespace in self.namespaces:
            object.__setattr__(original, '__dict__', namespace)
        for original, snapshot in self.pairs:
            if isinstance(original, dict):
                content = [(deepcopy(k, memo), deepcopy(v, memo)) for k, v in snapshot.items()]
                original.clear()
                original.update(content)
            elif isinstance(original, list):
                original[:] = [deepcopy(item, memo) for item in snapshot]
            elif isinstance(original, set):
                content = [deepcopy(item, memo) for item in snapshot]
                original.clear()
                original.update(content)
