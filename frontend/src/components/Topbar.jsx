
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search,
  ChevronDown,
  LogOut,
  UserRound,
  Languages,
  Check
} from "lucide-react";

import { useApp } from "../context/AppContext";
import { useLanguage } from "../context/LanguageContext";
import { schoolYearLabel } from "../schoolYear";

export default function Topbar({
  search = "",
  onSearch,
  placeholder
}) {
  const { user, logout } = useApp();
  const { language, setLanguage, t } = useLanguage();
  const navigate = useNavigate();

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const vi = language === "vi";

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointer = event => { if (!menuRef.current?.contains(event.target)) setMenuOpen(false); };
    const onKey = event => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  function handleLogout() {
    logout();
    setMenuOpen(false);
    navigate("/login", { replace: true });
  }

  function changeLanguage(value) {
    setLanguage(value);
    setMenuOpen(false);
  }

  return (
    <header className="topbar">
      {/* Search */}
      {onSearch ? (
        <div className="search-wrap glass">
          <Search size={19} />
          <input
            type="search"
            value={search}
            onChange={e => onSearch(e.target.value)}
            placeholder={placeholder || t("search")}
            aria-label={vi ? "Tìm kiếm" : "Search"}
          />
        </div>
      ) : <div className="topbar-spacer" />}

      {/* Profile */}
      <div className="coc-profile-wrapper" ref={menuRef}>
        <button
          type="button"
          className="profile glass"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          aria-label={vi ? "Tài khoản" : "Account"}
        >
          <span className="profile-avatar">
            {(user?.name || "U")[0].toUpperCase()}
          </span>

          <div>
            <small>{vi ? "Xin chào," : "Hello,"}</small>
            <strong>{user?.name || "User"}</strong>
          </div>

          <ChevronDown size={17} />
        </button>

        {menuOpen && (
          <div className="coc-profile-menu">
            {/* User */}
            <div className="coc-menu-user">
              <UserRound size={18} />
              <span>
                <strong>{user?.name || "Guest"}</strong>
                <small>{vi ? "Mã SV: " : "Student ID: "}{String(user?.studentId || user?.email || "—").toUpperCase()}</small>
                {(user?.major || user?.school_year) && (
                  <small>{[user.major, schoolYearLabel(user.school_year, language)].filter(Boolean).join(" · ")}</small>
                )}
              </span>
            </div>

            <div className="coc-menu-divider" />

            {/* Language */}
            <div className="coc-menu-language-title">
              <Languages size={17} />
              <span>{vi ? "Ngôn ngữ" : "Language"}</span>
            </div>

            <button
              type="button"
              className="coc-language-option"
              onClick={() => changeLanguage("vi")}
            >
              <span>Tiếng Việt</span>
              {language === "vi" && <Check size={17} />}
            </button>

            <button
              type="button"
              className="coc-language-option"
              onClick={() => changeLanguage("en")}
            >
              <span>English</span>
              {language === "en" && <Check size={17} />}
            </button>

            <div className="coc-menu-divider" />

            {/* Logout */}
            <button
              type="button"
              className="coc-logout-btn"
              onClick={handleLogout}
            >
              <LogOut size={18} />
              {t("logout")}
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
