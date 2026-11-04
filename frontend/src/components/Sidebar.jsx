import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  Home,
  Compass,
  Video,
  ChartNoAxesColumn,
  FolderOpen,
  LogOut
} from "lucide-react";

import { useApp } from "../context/AppContext";
import { useLanguage } from "../context/LanguageContext";
import "./Sidebar.css";

export default function Sidebar({ activeInterview = false } = {}) {
  const navigate = useNavigate();
  const { logout } = useApp();
  const { language } = useLanguage();

  const vi = language === "vi";

  const links = [
    {
      to: "/home",
      label: vi ? "Trang chủ" : "Home",
      icon: Home
    },
    {
      to: "/jobs",
      label: vi ? "Khám phá việc làm" : "Explore Jobs",
      icon: Compass
    },
    {
      to: "/prepare-interview",
      label: vi ? "Phỏng vấn" : "Interview",
      icon: Video
    },
    {
      to: "/cv-library",
      label: vi ? "Kho CV" : "CV Library",
      icon: FolderOpen
    },
    {
      to: "/results",
      label: vi ? "Kết quả" : "Results",
      icon: ChartNoAxesColumn
    }
  ];

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <aside className="sidebar glass">
      <div className="brand">
        <img className="brand-logo" src="/images/coc-logo-transparent.png" alt="" />
        <span>Cóc Đi Làm</span>
      </div>

      <nav className="nav-links">
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              "nav-item " + (isActive || (activeInterview && to === "/prepare-interview") ? "active" : "")
            }
          >
            <Icon size={20} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-promo">
        <div>Luyện phỏng vấn<br /><strong>Tự tin đi làm!</strong></div>
        <img src="/images/coc-logo-transparent.png" alt="" />
      </div>

      <button
        type="button"
        className="nav-item logout"
        onClick={handleLogout}
      >
        <LogOut size={19} />
        {vi ? "Đăng xuất" : "Sign out"}
      </button>
    </aside>
  );
}
