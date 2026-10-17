from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import quote, urlencode

from app.framework.contracts.action import BaseAction, ActionContext, ActionResult, WorkflowError
from app.framework.contracts.proxmox import NativeArguments
from app.framework.workflows.expressions import EXPRESSION, REFERENCE, resolve


PREFIX = 'proxmox.native.'
RESOURCE = re.compile(r'[A-Za-z][A-Za-z0-9_-]*')
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}


@dataclass(frozen=True)
class NativeCall:
    method: str
    segments: tuple[str, ...]


def parse_native_call(uses: str) -> NativeCall:
    if not uses.startswith(PREFIX):
        raise WorkflowError('Invalid native action namespace', 500)
    text = uses[len(PREFIX):]
    position = 0
    segments: list[str] = []
    while position < len(text):
        match = RESOURCE.match(text, position)
        if not match:
            raise WorkflowError(f'Invalid native action: {uses}', 500)
        name = match.group()
        position = match.end()
        if position == len(text):
            if name not in METHODS:
                raise WorkflowError('Native action must end with an HTTP method', 500)
            return NativeCall(name.upper(), tuple(segments))
        segments.append(name)
        if text[position] == '(':
            start = position + 1
            position = start
            quoted = False
            escaped = False
            while position < len(text):
                char = text[position]
                if escaped:
                    escaped = False
                elif char == '\\' and quoted:
                    escaped = True
                elif char == '"':
                    quoted = not quoted
                elif char == ')' and not quoted:
                    break
                position += 1
            if position == len(text):
                raise WorkflowError('Unclosed native resource argument', 500)
            argument = text[start:position].strip()
            expression = EXPRESSION.fullmatch(argument)
            if expression and REFERENCE.fullmatch(expression.group(1)):
                segments.append(argument)
            elif argument.startswith('"'):
                try:
                    literal = json.loads(argument)
                except ValueError as error:
                    raise WorkflowError('Invalid native resource literal', 500) from error
                if not isinstance(literal, str) or not literal:
                    raise WorkflowError('Resource literal must be a non-empty string', 500)
                # Wrap literal values so they are never interpreted as expressions.
                segments.append(json.dumps(literal))
            elif re.fullmatch(r'[0-9]+', argument):
                segments.append(argument)
            else:
                raise WorkflowError('Resource argument must be a reference, string or integer', 500)
            position += 1
        if position >= len(text) or text[position] != '.':
            raise WorkflowError('Invalid native resource separator', 500)
        position += 1
    raise WorkflowError('Native action is missing its HTTP method', 500)


class ProxmoxNativeAction(BaseAction):
    def __init__(self, call: NativeCall):
        self._call = call

    async def execute(self, args: Any, context: ActionContext) -> ActionResult:
        parts = []
        for segment in self._call.segments:
            value = json.loads(segment) if segment.startswith('"') else resolve(segment, context)
            if not isinstance(value, (str, int)) or isinstance(value, bool) or str(value) in {'', '.', '..'}:
                raise WorkflowError('Invalid native resource value')
            parts.append(quote(str(value), safe=''))
        path = '/' + '/'.join(parts)
        if isinstance(args, NativeArguments):
            body, headers, query = args.body, args.headers, args.query_string
        elif isinstance(args, dict):
            encoded = urlencode(args, doseq=True).encode('utf-8')
            if self._call.method in {'GET', 'HEAD', 'DELETE'}:
                body, headers, query = b'', (), encoded
            else:
                body, query = encoded, b''
                headers = ((b'content-type', b'application/x-www-form-urlencoded'),)
        else:
            raise WorkflowError('Native args must be API parameters or NativeArguments', 500)
        mutating = self._call.method not in {'GET', 'HEAD', 'OPTIONS'}
        if mutating and context.operation_id:
            await context.operations.mark_submitting(context.operation_id)
            context.submission_attempted = True
        response = await context.proxmox.request(
            self._call.method, path, query_string=query, headers=headers, body=body,
        )
        if mutating:
            context.upstream_response = response
        return ActionResult(response)
