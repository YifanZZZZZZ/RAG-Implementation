"""FAISS-only subprocess avoids conflicting PyTorch/FAISS OpenMP dylibs on macOS."""

import json, sys
import faiss
import numpy as np

faiss.omp_set_num_threads(1)
command, path = sys.argv[1:3]
if command == "build":
    matrix = np.load(sys.argv[3], allow_pickle=False)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(np.ascontiguousarray(matrix, dtype=np.float32))
    faiss.write_index(index, path)
elif command == "search":
    request = json.load(sys.stdin)
    index = faiss.read_index(path)
    if index.ntotal != request["count"] or index.d != request["dimension"]:
        raise ValueError("Index dimensions mismatch")
    query = np.ascontiguousarray(request["vectors"], dtype=np.float32)
    scores, ids = index.search(query, request["k"])
    print(json.dumps({"scores": scores.tolist(), "ids": ids.tolist()}))
else:
    raise ValueError(command)
