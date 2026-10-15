from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote_plus

from app.framework.contracts.action import WorkflowError
from app.framework.contracts.proxmox import NativeArguments
from app.framework.contracts.request import EndpointRequest


@dataclass(frozen=True)
class CloneIntent:
    source_node: str
    source_vmid: int
    newid: int
    mode: str
    requested_target: str | None
    params: dict[str, Any]
    encoding: str


def normalize_clone(request: EndpointRequest) -> CloneIntent:
    content_type = dict(request.headers).get(b'content-type', b'').decode('latin-1').split(';')[0].strip().lower()
    if content_type == 'application/json':
        try:
            params = json.loads(request.body)
        except (ValueError, UnicodeDecodeError) as error:
            raise WorkflowError('Invalid JSON clone request') from error
        if not isinstance(params, dict):
            raise WorkflowError('Clone body must be an object')
    elif content_type in {'application/x-www-form-urlencoded', ''}:
        try:
            pairs = parse_qsl(request.body.decode('utf-8'), keep_blank_values=True)
        except UnicodeDecodeError as error:
            raise WorkflowError('Invalid form clone request') from error
        params = dict(pairs)
        for field in ('newid', 'target', 'full', 'storage'):
            if sum(key == field for key, _ in pairs) > 1:
                raise WorkflowError(f'Duplicate clone parameter: {field}')
    else:
        raise WorkflowError('Clone supports JSON or form request bodies', 415)
    try:
        source_node = request.path_params['node']
        source_vmid = int(request.path_params['vmid'])
        raw_newid = params['newid']
        if isinstance(raw_newid, bool) or not re.fullmatch(r'[0-9]+', str(raw_newid)):
            raise ValueError()
        newid = int(raw_newid)
        if newid < 100 or source_vmid < 100:
            raise ValueError()
    except (KeyError, ValueError, TypeError) as error:
        raise WorkflowError('Clone requires a valid source VMID and explicit newid >= 100') from error
    target = params.get('target')
    if target is not None and (not isinstance(target, str) or not target):
        raise WorkflowError('Clone target must be a non-empty node name')
    return CloneIntent(source_node, source_vmid, newid, 'AUTO' if target == 'auto' else 'FIXED', target, params, content_type)


def build_clone_arguments(request: EndpointRequest, clone: CloneIntent, selected_node: str) -> NativeArguments:
    params = dict(clone.params)
    body = request.body
    if clone.mode == 'AUTO':
        params['target'] = selected_node
        if clone.encoding == 'application/json':
            body = json.dumps(params, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        else:
            # Preserve all unrelated form bytes, ordering and unknown fields.
            from urllib.parse import quote_plus
            parts = body.split(b'&')
            for index, part in enumerate(parts):
                name, separator, _ = part.partition(b'=')
                if unquote_plus(name.decode('utf-8')) == 'target':
                    parts[index] = name + b'=' + quote_plus(selected_node).encode('ascii')
            body = b'&'.join(parts)
    return NativeArguments(params, body, request.headers, request.raw_query_string)
