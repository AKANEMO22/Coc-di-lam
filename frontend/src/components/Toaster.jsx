import React, { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";

import { dismissToast, holdToast, subscribeToasts } from "@interview/toast";

const ICONS = { info: Info, success: CheckCircle2, warn: AlertTriangle, error: XCircle };

export default function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => subscribeToasts(setItems), []);

  return (
    <div className="toast-stack" aria-live="polite" aria-atomic="false">
      <AnimatePresence initial={false}>
        {items.map(item => {
          const Icon = ICONS[item.tone] || Info;
          return (
            <motion.div
              key={item.id}
              layout
              className={`toast-item toast-${item.tone}`}
              role={item.tone === "error" ? "alert" : "status"}
              initial={{ opacity: 0, x: 40, scale: 0.98 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }}
              transition={{ type: "spring", stiffness: 420, damping: 34 }}
              onPointerEnter={() => holdToast(item.id, true)}
              onPointerLeave={() => holdToast(item.id, false)}
            >
              <Icon size={19} className="toast-icon" aria-hidden="true" />
              <p>{item.message}</p>
              {item.action && (
                <button
                  type="button"
                  className="toast-action"
                  onClick={() => { item.action.onClick(); dismissToast(item.id); }}
                >
                  {item.action.label}
                </button>
              )}
              <button type="button" className="toast-close" aria-label="Đóng thông báo" onClick={() => dismissToast(item.id)}>
                <X size={15} />
              </button>
              {item.duration > 0 && (
                <span className="toast-progress" style={{ animationDuration: `${item.duration}ms` }} aria-hidden="true" />
              )}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
