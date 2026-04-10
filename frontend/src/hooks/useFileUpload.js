/**
 * useFileUpload — handles file selection, base64 encoding, validation.
 * Supports .p .i .xml .zip
 */
import { useCallback, useState } from "react";

const ALLOWED_EXTENSIONS = [".p", ".i", ".xml", ".zip"];

export function useFileUpload() {
  const [uploadedFiles, setUploadedFiles] = useState([]); // [{name, data (base64)}]

  const addFiles = useCallback((fileList) => {
    const files = Array.from(fileList);
    const invalid = files.filter(f => {
      const ext = "." + f.name.split(".").pop().toLowerCase();
      return !ALLOWED_EXTENSIONS.includes(ext);
    });
    if (invalid.length) {
      alert(`Unsupported file type(s): ${invalid.map(f => f.name).join(", ")}\nOnly .p .i .xml .zip are supported.`);
    }
    const valid = files.filter(f => {
      const ext = "." + f.name.split(".").pop().toLowerCase();
      return ALLOWED_EXTENSIONS.includes(ext);
    });

    valid.forEach(file => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const b64 = e.target.result.split(",")[1];
        setUploadedFiles(prev => [...prev, { name: file.name, data: b64 }]);
      };
      reader.readAsDataURL(file);
    });
  }, []);

  const removeFile = useCallback((idx) => {
    setUploadedFiles(prev => prev.filter((_, i) => i !== idx));
  }, []);

  const clearFiles = useCallback(() => {
    setUploadedFiles([]);
  }, []);

  return { uploadedFiles, addFiles, removeFile, clearFiles };
}
