
const DB_NAME = "coc-di-lam-cvs";
const STORE = "resumes";

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transaction(mode, action) {
  const db = await openDB();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const store = tx.objectStore(STORE);
      const request = action(store);

      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export const cvStorage = {
  async getAll() {
    const cvs = await transaction(
      "readonly",
      store => store.getAll()
    );
    return cvs.sort((a, b) => b.createdAt - a.createdAt);
  },

  get(id) {
    return transaction("readonly", store => store.get(id));
  },

  async add(file, role = "") {
    const extension = file.name.split(".").pop().toLowerCase();

    if (!["pdf", "docx"].includes(extension)) {
      throw new Error("Chỉ hỗ trợ file PDF hoặc DOCX.");
    }

    if (file.size > 10 * 1024 * 1024) {
      throw new Error("Dung lượng CV không được vượt quá 10MB.");
    }

    const cv = {
      id: crypto.randomUUID(),
      name: file.name,
      role: role.trim(),
      size: file.size,
      type: extension,
      createdAt: Date.now(),
      file
    };

    await transaction("readwrite", store => store.add(cv));
    return cv;
  },

  /** Merge `patch` into a stored CV (e.g. its analysis or saved interview details). */
  async update(id, patch) {
    const db = await openDB();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readwrite");
        const store = tx.objectStore(STORE);
        const read = store.get(id);
        let updated = null;
        read.onsuccess = () => {
          if (!read.result) { reject(new Error("CV not found")); return; }
          updated = { ...read.result, ...patch, updatedAt: Date.now() };
          store.put(updated);
        };
        tx.oncomplete = () => resolve(updated);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error("CV update aborted"));
      });
    } finally {
      db.close();
    }
  },

  remove(id) {
    return transaction("readwrite", store => store.delete(id));
  }
};
