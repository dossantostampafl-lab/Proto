import React from "react";
import { createRoot } from "react-dom/client";

import { ValidationPanel } from "./ValidationPanel";
import "./validation.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const root = document.getElementById("validation-root");

if (root) {
  createRoot(root).render(
    <React.StrictMode>
      <ValidationPanel apiBase={API_BASE} />
    </React.StrictMode>,
  );
}
