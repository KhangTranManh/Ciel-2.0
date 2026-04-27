"""BufferWriter — In-memory buffer that writes to disk only once."""
import os
from langchain_core.tools import tool

from ..utils.logger import log


class BufferWriter:
    """Accumulates Worker output in memory. Flushes to disk in a single I/O op."""

    def __init__(self):
        self._buffer: list[str] = []

    def append(self, content: str) -> str:
        """Add content to the in-memory buffer. No disk I/O."""
        self._buffer.append(content)
        log.tool(f"Buffered {len(content)} chars (total chunks: {len(self._buffer)})")
        return f"Buffered successfully. Total chunks: {len(self._buffer)}"

    def flush(self, filepath: str) -> str:
        """Join buffer and write to disk in one single operation."""
        if not self._buffer:
            log.error("Buffer is empty — nothing to flush.")
            return "Error: buffer is empty."

        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

        combined = "\n".join(self._buffer)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(combined)

        size = len(combined)
        chunks = len(self._buffer)
        self._buffer.clear()

        log.tool(f"Flushed {chunks} chunks ({size} chars) to: {filepath}")
        return f"Written {size} chars to {filepath}"

    def get_content(self) -> str:
        """Return current buffer content without flushing."""
        return "\n".join(self._buffer)

    def clear(self):
        """Discard buffer without writing."""
        self._buffer.clear()


# Singleton instance shared across the graph
buffer_writer = BufferWriter()


@tool
def buffer_write(content: str) -> str:
    """Append the Worker's generated content to an in-memory buffer. Call this after the Worker produces output. The buffer will be flushed to disk when the entire task is complete."""
    return buffer_writer.append(content)
