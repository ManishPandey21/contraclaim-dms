"""The upload surface, derived from source rather than described in prose.

Gate 5 bullet 3 claims a property - *every upload-capable route enforces a
server-side size limit, and nothing materialises a whole body before that limit
decides* - and until R-A8U the guard behind it recognised three syntactic
shapes. The independent security review of R-A8T Part 2 returned two findings
against exactly that gap:

* **F-A8T2-5** - the guard watched only parameters whose *annotation* mentions
  `UploadFile`. `FileService.store_file(self, upload_file, ...)` is unannotated
  and does `await upload_file.read()` inside a scanned tree, and the guard was
  green over it.
* **F-A8T2-6** - `if node.args: continue` treated *any* argument as "a chunk was
  asked for", so `await file.read(-1)` - Starlette's own default, which reads
  the whole body - passed. So did an alias, a subscript and an `enumerate` loop
  variable.

Neither had a live bypass behind it. Both are the same class of defect: the
guard measured **spellings**, and the bullet claims a **property**. This module
measures the property, within a stated and conservative boundary:

* an upload parameter is recognised by its annotation **or** by its name, so
  deleting a type hint does not delete the parameter from scope;
* the value is followed through assignment, tuple unpacking, `for` and `with`
  targets, subscripts and attribute chains, so an alias is still the upload;
* a read is bounded only by a size argument that is **not** absent, `None`,
  negative, or a constant larger than the configured cap. A *computed* size is
  accepted, and that is the honest edge of a static check - the runtime
  boundary tests in `test_upload_limit_enforcement.py` are what cover the
  counting;
* a whole-request read (`await request.body()`) counts as materialising a body,
  because a route that never names `UploadFile` can still hold one.

Nothing here is a substitute for the runtime tests. It is the denominator: it
answers "which routes must be bounded", so a new one cannot join the codebase
without joining the inventory.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

RBAC_BACKEND = Path(__file__).resolve().parents[1]
ROUTERS = RBAC_BACKEND / "routers"

#: Trees where an upload can be read. F-A8T-1 was in `services/`, which the
#: first cut of the class guard did not look at.
UPLOAD_TREES: Tuple[Path, ...] = (
    ROUTERS,
    RBAC_BACKEND / "services",
    RBAC_BACKEND / "utils",
)

#: Bare parameter names that carry an upload with no annotation to say so.
#: `chunk` is here because `POST /api/contracts/upload-chunk` names its part
#: that way; `photo` and `image` because the profile surface does.
UPLOAD_PARAMETER_NAMES = frozenset(
    {
        "file",
        "files",
        "upload",
        "uploads",
        "attachment",
        "attachments",
        "chunk",
        "photo",
        "image",
    }
)

#: The seams that own the size decision. A call to one of these IS the limit.
BOUNDED_READ_SEAMS = frozenset(
    {
        "read_upload_within_limit",
        "read_file_within_limit",
        "read_request_body_within_limit",
        "spool_upload_file",
    }
)

#: Calls that write an upload somewhere durable. Used only by the inventory, to
#: answer "did anything persist before the limit decided?".
PERSISTENCE_CALLS = frozenset(
    {
        "insert_one",
        "insert_many",
        "replace_one",
        "update_one",
        "upsert",
        "write_bytes",
        "write_text",
        "upload_bytes",
        "upload_fileobj",
        "put_object",
        "store_file",
        "store_chunk",
        "store_chunk_bytes",
        "save_summary",
    }
)


#: Suffixes that describe an upload without being one. `uploaded_by` is a user
#: id and `upload_id` is a session key; both are `str` form fields on routes
#: that never touch a body. Without this the name heuristic reports nine `GET`
#: routes as upload surfaces, and an inventory full of noise gets ignored.
NOT_AN_UPLOAD_SUFFIXES = (
    "_by",
    "_id",
    "_ids",
    "_at",
    "_date",
    "_count",
    "_name",
    "_names",
    "_path",
    "_paths",
    "_url",
    "_urls",
    "_type",
    "_types",
    "_status",
    "_size",
    "_mode",
    "_key",
    "_keys",
)

#: Annotations that settle the question on their own: a `str` is not an upload
#: however it is named.
SCALAR_ANNOTATIONS = (
    "str",
    "int",
    "float",
    "bool",
    "bytes",
    "datetime",
    "date",
    "uuid",
    "dict",
    "any",
)


def _annotation_excludes_an_upload(annotation: str) -> bool:
    if not annotation:
        return False
    if "UploadFile" in annotation:
        return False
    stripped = annotation.lower()
    for wrapper in ("optional[", "list[", "sequence[", "union[", "annotated[", "|", "]", "[", " "):
        stripped = stripped.replace(wrapper, ",")
    parts = {part for part in stripped.split(",") if part}
    return bool(parts) and parts <= set(SCALAR_ANNOTATIONS) | {"none"}


def looks_like_an_upload_parameter(name: str, annotation: str = "") -> bool:
    """True when this parameter carries an upload even unannotated.

    F-A8T2-5. Annotation-only recognition made `store_file(self, upload_file)`
    invisible; a name test cannot be defeated by deleting a type hint. The two
    exclusions below are what keep the name test from being noise rather than a
    guard: an annotation that settles the question, and the metadata suffixes
    (`upload_id`, `uploaded_by`) that name an upload without holding one.
    """
    if "UploadFile" in annotation:
        return True
    if _annotation_excludes_an_upload(annotation):
        return False
    lowered = name.lower().lstrip("_")
    if lowered.endswith(NOT_AN_UPLOAD_SUFFIXES):
        return False
    if "upload" in lowered:
        return True
    if lowered in UPLOAD_PARAMETER_NAMES:
        return True
    return lowered.endswith(("_file", "_files"))


def _base_name(node: ast.AST) -> Optional[str]:
    """The root name an attribute/subscript chain hangs off: `a.b[0].c` -> `a`."""
    current: ast.AST = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _bound_names(target: ast.AST) -> Set[str]:
    return {child.id for child in ast.walk(target) if isinstance(child, ast.Name)}


def _constant_int(node: ast.AST) -> Optional[int]:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _constant_int(node.operand)
        return None if inner is None else -inner
    return None


def read_is_bounded(call: ast.Call, cap_bytes: int) -> Tuple[bool, str]:
    """Is this `.read(...)` bounded by a size the caller can defend?

    Returns `(bounded, why_not)`. The unbounded forms, each of which reads the
    whole body:

    * `read()` - no size at all;
    * `read(None)` - Starlette spells "everything" this way;
    * `read(-1)` - and so does the standard file protocol. **F-A8T2-6**;
    * `read(1_000_000_000)` - a number larger than the configured cap is a
      whole-body read wearing a size argument.

    A *computed* size (`read(chunk_size)`) is accepted. That is deliberately
    generous and deliberately stated: a static check cannot evaluate it, and
    pretending otherwise would either ban the correct seam or invent a value.
    """
    argument: Optional[ast.expr] = call.args[0] if call.args else None
    if argument is None:
        for keyword in call.keywords:
            if keyword.arg in {"size", "n", "amt"}:
                argument = keyword.value
                break
    if argument is None:
        return False, "read() with no size argument reads the whole body"
    if isinstance(argument, ast.Constant) and argument.value is None:
        return False, "read(None) reads the whole body"
    value = _constant_int(argument)
    if value is None:
        return True, ""
    if value < 0:
        return False, f"read({value}) reads the whole body"
    if value > cap_bytes:
        return False, (
            f"read({value}) asks for more than the configured cap of {cap_bytes} bytes"
        )
    return True, ""


class UploadTaint:
    """Every name in one module that can hold an upload.

    Intra-module, flow-insensitive and conservative: a name that is *ever*
    assigned from an upload is treated as holding one everywhere.
    Over-approximating is the safe direction for a guard whose failure mode is a
    missed whole-body read.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.tainted: Set[str] = set()
        self._seed_parameters(tree)
        self._propagate(tree)

    def _seed_parameters(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            arguments = (
                list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            )
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            for argument in arguments:
                annotation = ast.unparse(argument.annotation) if argument.annotation else ""
                if looks_like_an_upload_parameter(argument.arg, annotation):
                    self.tainted.add(argument.arg)

    def _propagate(self, tree: ast.Module) -> None:
        # A fixpoint rather than one pass: `a = file; b = a; c = b` needs three.
        for _ in range(8):
            grew = False
            for node in ast.walk(tree):
                sources: List[ast.AST] = []
                targets: List[ast.AST] = []
                if isinstance(node, ast.Assign):
                    sources, targets = [node.value], list(node.targets)
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                    sources, targets = [node.value], [node.target]
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    sources, targets = [node.iter], [node.target]
                elif isinstance(node, ast.withitem):
                    if node.optional_vars is None:
                        continue
                    sources, targets = [node.context_expr], [node.optional_vars]
                elif isinstance(node, ast.comprehension):
                    sources, targets = [node.iter], [node.target]
                else:
                    continue

                if not any(self.holds_an_upload(source) for source in sources):
                    continue
                for target in targets:
                    for name in _bound_names(target):
                        if name not in self.tainted:
                            self.tainted.add(name)
                            grew = True
            if not grew:
                return

    def holds_an_upload(self, node: ast.AST) -> bool:
        """Does this expression evaluate to an upload, or to something holding one?

        `file`, `file.file`, `files[0]`, `enumerate(files)` and `zip(files, names)`
        all do. A call to a bounded seam does **not**:
        `read_upload_within_limit(file, cap)` returns bytes already measured.
        """
        if isinstance(node, ast.Call):
            callee = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            if callee in BOUNDED_READ_SEAMS:
                return False
            if callee in {"enumerate", "zip", "list", "tuple", "iter", "reversed", "sorted"}:
                return any(self.holds_an_upload(argument) for argument in node.args)
            return False
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return _base_name(node) in self.tainted
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(self.holds_an_upload(element) for element in node.elts)
        return False


def uncapped_upload_reads(
    source: str,
    label: str = "<source>",
    *,
    cap_bytes: Optional[int] = None,
) -> List[str]:
    """Every place `source` materialises an upload body with no defensible limit.

    Three channels, because the review found the guard could see only one class
    of them:

    * `<upload>.read()` and its aliases - including the unannotated parameter of
      **F-A8T2-5** and the `read(-1)` of **F-A8T2-6**;
    * `<upload>.file.read()` - the synchronous spooled handle (F-A8T-1);
    * `await request.body()` - a route that never mentions `UploadFile` and
      still holds the whole request. `routers/billing_webhooks.py` was doing
      exactly that on an **unauthenticated** endpoint while this guard was green.
    """
    if cap_bytes is None:
        from rbac_backend.core.config import settings  # noqa: PLC0415

        cap_bytes = max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024

    tree = ast.parse(source)
    taint = UploadTaint(tree)
    offenders: List[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue

        if node.func.attr == "body" and not node.args:
            rendered = ast.unparse(node.func.value)
            if "request" in rendered.lower():
                offenders.append(
                    f"{label}:{node.lineno}: {rendered}.body() materialises the whole "
                    "request body with no size limit"
                )
            continue

        if node.func.attr != "read":
            continue
        if not taint.holds_an_upload(node.func.value):
            continue
        bounded, why_not = read_is_bounded(node, cap_bytes)
        if not bounded:
            offenders.append(
                f"{label}:{node.lineno}: {ast.unparse(node.func.value)}.{why_not}"
            )

    return offenders


def scan_upload_trees(trees: Sequence[Path] = UPLOAD_TREES) -> List[str]:
    """The class guard, over every module that can hold an upload."""
    offenders: List[str] = []
    for root in trees:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            # utf-8-sig: four service modules carry a BOM, which Python strips
            # on import and `ast.parse` refuses. Read the way the interpreter
            # does, or the guard skips them with a crash.
            offenders.extend(
                uncapped_upload_reads(
                    path.read_text(encoding="utf-8-sig"),
                    str(path.relative_to(RBAC_BACKEND)),
                )
            )
    return offenders


# --------------------------------------------------------------------------- #
# The inventory: which routes are upload-capable, and how each one is bounded
# --------------------------------------------------------------------------- #


HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

#: The value every column carries when the classifier cannot answer it. The
#: inventory test refuses to pass while any row holds it: a route whose
#: enforcement cannot be read from source is exactly the route that escapes a
#: guard silently.
UNCLASSIFIABLE = "UNCLASSIFIABLE"


@dataclass(frozen=True)
class UploadRoute:
    """One upload-capable route, classified from source."""

    module: str
    method: str
    path: str
    function: str
    upload_parameters: Tuple[str, ...]
    read_method: str
    limit_source: str
    limit_before_read: str
    persistence_before_limit: str
    analysed_through: Tuple[str, ...]

    @property
    def is_classifiable(self) -> bool:
        return UNCLASSIFIABLE not in (
            self.read_method,
            self.limit_source,
            self.limit_before_read,
            self.persistence_before_limit,
        )


@dataclass
class _Frame:
    """One function body, with everything the classifier needs to judge it."""

    module: str
    qualname: str
    node: ast.AST
    taint: "UploadTaint"


class CallGraph:
    """Functions and methods across the upload trees, resolvable by call site.

    A route rarely does its own reading. `POST /api/documents` hands the file to
    `DocumentController.create_document`; `POST /api/documents/bulk-upload`
    reaches the CSV cap three hops away in
    `BulkUploadService.read_csv_from_upload_file`; the canonical Insurance
    upload delegates into a controller defined in **another module**. A
    classifier that follows one same-module hop reports all three as
    "unclassifiable", which is indistinguishable from "unbounded" and therefore
    useless as a signal.

    Resolution is deliberately simple and deliberately fails loudly:

    * `name(...)` resolves against module-level functions of that name;
    * `receiver.name(...)` resolves against **methods** of that name, narrowed by
      the annotated class of `receiver` when the call site declares one;
    * anything that stays ambiguous is reported as ambiguous rather than picked.
    """

    def __init__(self, roots: Sequence[Path]) -> None:
        self.functions: Dict[str, List[_Frame]] = {}
        self.methods: Dict[str, List[_Frame]] = {}
        self.by_class: Dict[Tuple[str, str], _Frame] = {}
        self.trees: Dict[str, ast.Module] = {}
        for root in roots:
            paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
            for path in paths:
                if "__pycache__" in path.parts:
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                except SyntaxError:  # pragma: no cover - a broken module is a build failure
                    continue
                module = path.name
                self.trees[module] = tree
                taint = UploadTaint(tree)
                self._index(tree, module, taint, enclosing=None)

    def _index(
        self,
        node: ast.AST,
        module: str,
        taint: "UploadTaint",
        enclosing: Optional[str],
    ) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self._index(child, module, taint, enclosing=child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{enclosing}.{child.name}" if enclosing else child.name
                frame = _Frame(module=module, qualname=qualname, node=child, taint=taint)
                if enclosing:
                    self.methods.setdefault(child.name, []).append(frame)
                    self.by_class[(enclosing, child.name)] = frame
                else:
                    self.functions.setdefault(child.name, []).append(frame)
                self._index(child, module, taint, enclosing)
            else:
                self._index(child, module, taint, enclosing)

    def resolve(
        self,
        call: ast.Call,
        annotations: Dict[str, str],
        module: str = "",
    ) -> Tuple[Optional[_Frame], bool]:
        """`(frame, ambiguous)` for one call site.

        `module` is the caller's own module, and it breaks the commonest tie:
        five routers each define a private `_read_csv(file)` helper, so a
        name-only lookup finds five candidates, calls the delegate ambiguous and
        stops - which reported every CSV import route as "never read by the
        handler" while each of them was reading, and bounding, a CSV.
        """
        func = call.func
        if isinstance(func, ast.Name):
            candidates = self.functions.get(func.id, [])
        elif isinstance(func, ast.Attribute):
            receiver = _base_name(func.value)
            declared = annotations.get(receiver or "", "")
            if declared and (declared, func.attr) in self.by_class:
                return self.by_class[(declared, func.attr)], False
            candidates = self.methods.get(func.attr, [])
            if not candidates:
                candidates = self.functions.get(func.attr, [])
        else:
            return None, False

        if len(candidates) == 1:
            return candidates[0], False
        if len(candidates) > 1:
            local = [frame for frame in candidates if frame.module == module]
            if len(local) == 1:
                return local[0], False
            return None, True
        return None, False


def _parameter_annotations(node: ast.AST) -> Dict[str, str]:
    """Parameter name -> the bare class its annotation names, for delegate lookup."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {}
    found: Dict[str, str] = {}
    for argument in list(node.args.args) + list(node.args.kwonlyargs):
        if argument.annotation is None:
            continue
        rendered = ast.unparse(argument.annotation)
        bare = rendered.split("[")[0].split(".")[-1].strip()
        if bare:
            found[argument.arg] = bare
    return found


def _router_prefix(tree: ast.Module) -> str:
    """The `APIRouter(prefix=...)` this module's routes hang under.

    Without it the inventory reports `/webhooks/{provider}` for a route mounted
    at `/billing/webhooks/{provider}`, and a declared-route list built from the
    real API would never match.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
        )
        if callee != "APIRouter":
            continue
        for keyword in node.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return ""


def _route_decorators(node: ast.AST, prefix: str = "") -> Iterable[Tuple[str, str]]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute) or func.attr.lower() not in HTTP_METHODS:
            continue
        if not decorator.args:
            continue
        first = decorator.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield func.attr.upper(), prefix + first.value
        else:
            yield func.attr.upper(), UNCLASSIFIABLE


def _request_parameters(node: ast.AST) -> Set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    found: Set[str] = set()
    for argument in list(node.args.args) + list(node.args.kwonlyargs):
        annotation = ast.unparse(argument.annotation) if argument.annotation else ""
        if "Request" in annotation and "UploadFile" not in annotation:
            found.add(argument.arg)
    return found


def _upload_parameters(node: ast.AST) -> Tuple[str, ...]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ()
    names: List[str] = []
    for argument in list(node.args.args) + list(node.args.kwonlyargs):
        annotation = ast.unparse(argument.annotation) if argument.annotation else ""
        if looks_like_an_upload_parameter(argument.arg, annotation):
            names.append(argument.arg)
    return tuple(names)


#: The ways a handler can take the whole request body without an `UploadFile`.
REQUEST_BODY_READS = frozenset({"body", "stream", "form", "json"})

#: Method names that belong to containers, strings and models rather than to
#: this codebase. Chasing `headers.get` or `payload.items` as a delegate finds
#: dozens of same-named methods and reports an ambiguity that means nothing.
NON_DELEGATE_METHODS = frozenset(
    {
        "add", "append", "copy", "count", "decode", "dict", "discard",
        "encode", "endswith", "extend", "format", "get", "index", "isoformat",
        "items", "join", "json", "keys", "lower", "model_dump", "pop",
        "remove", "replace", "set", "sort", "split", "startswith", "strip",
        "update", "upper", "values",
    }
)


def _request_body_parameters(node: ast.AST) -> Tuple[str, ...]:
    """`Request` parameters this handler actually reads the body of.

    A `Request` consulted only for headers, the client address or app state is
    not an upload surface. One asked for `.body()` holds a whole
    client-supplied payload, which is the property the bullet is about however
    the payload arrived.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ()
    candidates = _request_parameters(node)
    if not candidates:
        return ()
    used: List[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        callee = (
            child.func.attr
            if isinstance(child.func, ast.Attribute)
            else getattr(child.func, "id", None)
        )
        if isinstance(child.func, ast.Attribute) and callee in REQUEST_BODY_READS:
            base = _base_name(child.func.value)
            if base in candidates and base not in used:
                used.append(base)
        elif callee in BOUNDED_READ_SEAMS:
            # A request handed to the bounded seam is still a body-reading
            # route. Without this the webhook drops OUT of the inventory the
            # moment it is fixed, and the denominator quietly shrinks by the one
            # row that was hardest to find.
            for argument in list(child.args) + [kw.value for kw in child.keywords]:
                base = _base_name(argument)
                if base in candidates and base not in used:
                    used.append(base)
    return tuple(used)


def _limit_expression(call: ast.Call) -> Optional[str]:
    """The size the bounded seam was handed, rendered as it appears in source."""
    for keyword in call.keywords:
        if keyword.arg in {"max_size_bytes", "max_bytes", "limit"}:
            return ast.unparse(keyword.value)
    if len(call.args) >= 2:
        return ast.unparse(call.args[1])
    return None


#: How far the classifier will chase a delegate. Three is what the real call
#: chains need: route -> controller -> service -> seam.
MAX_DELEGATE_DEPTH = 3


def _reaches_seam(
    frame: _Frame,
    graph: CallGraph,
    memo: Dict[Tuple[str, str], bool],
    stack: Set[Tuple[str, str]],
    depth: int = 0,
) -> bool:
    """Does this frame, or anything it calls, bound an upload?

    Needed to answer "before the limit" honestly. The seam is often not in the
    frame that persists, nor in the frame the route body shows: five CSV import
    routes call a module-private `_read_csv(file)` that owns the cap, so the
    route's *own* first seam line is `None` and every subsequent write looked
    like a write before the limit. What matters is the line at which the frame
    delegated to something that bounds - after that call returns, the body has
    been measured.
    """
    key = (frame.module, frame.qualname)
    if key in memo:
        return memo[key]
    if key in stack or depth > MAX_DELEGATE_DEPTH:
        return False
    stack.add(key)
    annotations = _parameter_annotations(frame.node)
    found = False
    for child in ast.walk(frame.node):
        if not isinstance(child, ast.Call):
            continue
        callee = (
            child.func.attr
            if isinstance(child.func, ast.Attribute)
            else getattr(child.func, "id", None)
        )
        if callee in BOUNDED_READ_SEAMS:
            found = True
            break
        if callee in NON_DELEGATE_METHODS or callee in PERSISTENCE_CALLS:
            continue
        resolved, _ = graph.resolve(child, annotations, frame.module)
        if resolved is not None and _reaches_seam(resolved, graph, memo, stack, depth + 1):
            found = True
            break
    stack.discard(key)
    memo[key] = found
    return found


@dataclass
class _Evidence:
    seams: List[str]
    limits: List[str]
    through: List[str]
    unbounded_reads: List[str]
    persistence_before_seam: List[str]
    ambiguous: List[str]
    touched_a_body: bool


def _walk_frame(
    frame: _Frame,
    graph: CallGraph,
    evidence: _Evidence,
    depth: int,
    seen: Set[Tuple[str, str]],
    already_bounded: bool = False,
    memo: Optional[Dict[Tuple[str, str], bool]] = None,
) -> None:
    """Collect what bounds this frame, then follow its delegates."""
    key = (frame.module, frame.qualname)
    if key in seen or depth > MAX_DELEGATE_DEPTH:
        return
    seen.add(key)
    if memo is None:
        memo = {}
    evidence.through.append(f"{frame.module}::{frame.qualname}")

    annotations = _parameter_annotations(frame.node)
    request_names = _request_parameters(frame.node)
    first_seam: Optional[int] = None
    persistence: List[int] = []
    delegates: List[ast.Call] = []

    for child in ast.walk(frame.node):
        if not isinstance(child, ast.Call):
            continue
        callee = (
            child.func.attr
            if isinstance(child.func, ast.Attribute)
            else getattr(child.func, "id", None)
        )
        if callee in BOUNDED_READ_SEAMS:
            evidence.seams.append(callee)
            evidence.touched_a_body = True
            first_seam = child.lineno if first_seam is None else min(first_seam, child.lineno)
            rendered = _limit_expression(child)
            if rendered:
                evidence.limits.append(rendered)
            continue
        if isinstance(child.func, ast.Attribute) and callee == "read":
            if frame.taint.holds_an_upload(child.func.value):
                evidence.touched_a_body = True
                bounded, why_not = read_is_bounded(child, _configured_cap_bytes())
                if not bounded:
                    evidence.unbounded_reads.append(
                        f"{frame.module}::{frame.qualname}:{child.lineno}: {why_not}"
                    )
            continue
        if isinstance(child.func, ast.Attribute) and callee in REQUEST_BODY_READS:
            if _base_name(child.func.value) in request_names:
                evidence.touched_a_body = True
                evidence.unbounded_reads.append(
                    f"{frame.module}::{frame.qualname}:{child.lineno}: "
                    f"request.{callee}() materialises the whole body"
                )
            continue
        if callee in PERSISTENCE_CALLS:
            persistence.append(child.lineno)
            continue
        if callee in NON_DELEGATE_METHODS:
            continue
        delegates.append(child)

    # "Before the limit" is a question about order, and order only exists inside
    # one frame. A callee invoked *after* this frame bounded the upload is
    # downstream work: the document row `DocumentService` writes is written from
    # an already-measured body, and counting it here reported every real upload
    # route as persisting before its own limit.
    resolved_delegates: List[Tuple[ast.Call, _Frame]] = []
    for call in delegates:
        resolved, ambiguous = graph.resolve(call, annotations, frame.module)
        if ambiguous:
            rendered = ast.unparse(call.func)
            evidence.ambiguous.append(f"{frame.module}::{frame.qualname}: {rendered}")
            continue
        if resolved is not None:
            resolved_delegates.append((call, resolved))

    # The line at which this frame's upload becomes measured: its own earliest
    # seam call, or the earliest delegate call that reaches one.
    seam_line = first_seam
    for call, target in resolved_delegates:
        if _reaches_seam(target, graph, memo, set()):
            seam_line = call.lineno if seam_line is None else min(seam_line, call.lineno)

    # Only a frame that HOLDS the upload can persist it. `ContractService`'s
    # session bookkeeping, the usage meter and the EOT revision writer all take
    # identifiers and parsed rows, never the body, so a write of theirs is not a
    # partial persistence of a rejected upload - and counting it reported eight
    # correctly-bounded routes as persisting before their own limit. A guard
    # that cries about eight correct routes is a guard that gets deleted.
    if not already_bounded and _upload_parameters(frame.node):
        for line in persistence:
            if seam_line is None or line < seam_line:
                evidence.persistence_before_seam.append(
                    f"{frame.module}::{frame.qualname}:{line}"
                )

    for call, target in resolved_delegates:
        _walk_frame(
            target,
            graph,
            evidence,
            depth + 1,
            seen,
            already_bounded=already_bounded
            or (seam_line is not None and seam_line <= call.lineno),
            memo=memo,
        )


def _configured_cap_bytes() -> int:
    from rbac_backend.core.config import settings  # noqa: PLC0415

    return max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024


def upload_routes(routers: Path = ROUTERS, graph: Optional[CallGraph] = None) -> List[UploadRoute]:
    """Every upload-capable route in the API, derived from the router modules.

    "Upload-capable" is not "mentions UploadFile": a route that takes a raw
    `Request` and reads its body holds a whole client-supplied payload just the
    same, and `POST /api/billing/webhooks/{provider}` is unauthenticated.
    """
    if graph is None:
        graph = CallGraph((routers, RBAC_BACKEND / "services", RBAC_BACKEND / "utils"))

    routes: List[UploadRoute] = []
    for path in sorted(routers.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        taint = UploadTaint(tree)
        prefix = _router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = list(_route_decorators(node, prefix))
            if not decorators:
                continue
            parameters = _upload_parameters(node) + _request_body_parameters(node)
            if not parameters:
                continue

            evidence = _Evidence([], [], [], [], [], [], False)
            _walk_frame(
                _Frame(module=path.name, qualname=node.name, node=node, taint=taint),
                graph,
                evidence,
                depth=0,
                seen=set(),
            )

            seams = list(dict.fromkeys(evidence.seams))
            limits = list(dict.fromkeys(evidence.limits))

            if seams:
                read_method = ", ".join(seams)
                limit_source = ", ".join(limits) if limits else UNCLASSIFIABLE
                limit_before_read = (
                    "yes"
                    if not evidence.unbounded_reads
                    else "no: " + "; ".join(evidence.unbounded_reads)
                )
            elif not evidence.touched_a_body:
                # The handler accepts an upload and never reads it. `POST
                # /api/insurance/upload` is a 410 stub kept so existing clients
                # get a reason rather than a 404. Nothing is materialised by the
                # application, so there is nothing for a limit to bound.
                read_method = "never read by the handler"
                limit_source = "not applicable - no body is materialised"
                limit_before_read = "not applicable"
            else:
                read_method = UNCLASSIFIABLE
                limit_source = UNCLASSIFIABLE
                limit_before_read = UNCLASSIFIABLE

            if not evidence.touched_a_body:
                persistence_before_limit = "not applicable"
            elif evidence.persistence_before_seam:
                persistence_before_limit = (
                    "yes: " + "; ".join(evidence.persistence_before_seam)
                    if seams
                    else UNCLASSIFIABLE
                )
            else:
                persistence_before_limit = "no"

            through = tuple(dict.fromkeys(evidence.through))
            if evidence.ambiguous:
                through = through + tuple(f"AMBIGUOUS:{item}" for item in evidence.ambiguous)

            for method, route_path in decorators:
                routes.append(
                    UploadRoute(
                        module=path.name,
                        method=method,
                        path=route_path,
                        function=node.name,
                        upload_parameters=parameters,
                        read_method=read_method,
                        limit_source=limit_source,
                        limit_before_read=limit_before_read,
                        persistence_before_limit=persistence_before_limit,
                        analysed_through=through,
                    )
                )
    return routes


def render_inventory(routes: Sequence[UploadRoute]) -> str:
    """The inventory as a Markdown table, for a receipt or an evidence bundle."""
    header = (
        "| ROUTE | METHOD | UPLOAD PARAMETER | READ METHOD | LIMIT SOURCE | "
        "LIMIT BEFORE READ? | PERSISTENCE BEFORE LIMIT? |\n"
        "|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for route in routes:
        lines.append(
            f"| `{route.path}` | {route.method} | "
            f"{', '.join(route.upload_parameters)} | {route.read_method} | "
            f"{route.limit_source} | {route.limit_before_read} | "
            f"{route.persistence_before_limit} |"
        )
    return "\n".join(lines)
