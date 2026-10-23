import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';
export default function Modal({ title, eyebrow, description, children, onClose }: { title: string; eyebrow: string; description: string; children: ReactNode; onClose: () => void }) {
 const ref = useRef<HTMLDialogElement>(null);
 useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
 return <dialog ref={ref} className="metadata-dialog" aria-labelledby="modal-title" onCancel={onClose} onClick={e => { if (e.target === ref.current) { const rect = ref.current!.getBoundingClientRect(); if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) onClose(); } }}><div className="metadata-header"><div><div className="eyebrow">{eyebrow}</div><h2 id="modal-title">{title}</h2></div><button className="dialog-close" aria-label="Close details" onClick={onClose}><X size={17} /></button></div><p>{description}</p>{children}</dialog>;
}
