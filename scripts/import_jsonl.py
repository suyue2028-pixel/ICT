"""Same import implementation as ict-rag, retained as a standalone demo entry."""
import sys
from rag_agent.cli import main

if __name__ == "__main__":
    sys.argv.insert(1, "import")
    main()
