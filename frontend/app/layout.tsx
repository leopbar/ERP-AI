import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {title:'ERP AI · Consultoria de parametrização',description:'Atendimentos, propostas e conhecimento funcional do HCM Lab'};
export default function Layout({children}:{children:React.ReactNode}) {return <html lang="pt-BR"><body>{children}</body></html>}
