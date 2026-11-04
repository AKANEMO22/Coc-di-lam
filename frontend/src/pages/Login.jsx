import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CalendarRange,
  Eye,
  EyeOff,
  GraduationCap,
  IdCard,
  Languages,
  Loader2,
  LockKeyhole,
  Mail,
  UserRound
} from "lucide-react";

import { api } from "@interview/api";
import { saveLogin } from "@interview/auth";
import { useApp } from "../context/AppContext";
import { useLanguage } from "../context/LanguageContext";
import { SCHOOL_YEARS, schoolYearLabel } from "../schoolYear";

const EMPTY_PROFILE = { name: "", email: "", major: "", school_year: "" };
// Matches the laptop zoom (coc-camera-push in styles.css) before the workspace shows.
const ENTER_DELAY = 950;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function cleanError(err) {
  const text = err instanceof Error ? err.message : String(err);
  return text.replace(/^\d{3}:\s*/, "");
}

export default function Login() {
  const { login } = useApp();
  const { language, setLanguage } = useLanguage();
  const navigate = useNavigate();

  // "id": only the student ID (+ the class access code when the gateway asks for one).
  // "profile": first sign-in of this ID — ask for the name and a few details, once.
  const [step, setStep] = useState("id");
  const [studentId, setStudentId] = useState("");
  const [accessCode, setAccessCode] = useState("");
  const [profile, setProfile] = useState(EMPTY_PROFILE);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(false);
  const [welcome, setWelcome] = useState("");
  const [gate, setGate] = useState({ auth_required: false, access_code_required: false, mode: "off" });

  const timer = useRef(null);
  const idInput = useRef(null);
  const nameInput = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    let active = true;
    api.authStatus().then(status => { if (active) setGate(status); }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const target = step === "profile" ? nameInput : idInput;
    const id = window.setTimeout(() => target.current?.focus(), 240);
    return () => window.clearTimeout(id);
  }, [step]);

  const vi = language === "vi";
  const needsCode = gate.mode === "student" && gate.access_code_required;
  const locked = busy || zoom;

  function changeLanguage() {
    if (zoom) return;
    setLanguage(vi ? "en" : "vi");
    setError("");
  }

  function setField(key, value) {
    setProfile(current => ({ ...current, [key]: value }));
    setError("");
  }

  function enter(result, fallbackName) {
    const info = result.profile || {};
    const name = info.name || fallbackName || result.student_id;
    if (result.access_token) saveLogin(result, name);
    setWelcome(name);
    setError("");
    setZoom(true);
    timer.current = setTimeout(() => {
      login({
        studentId: result.student_id,
        name,
        email: info.email || "",
        major: info.major || "",
        school_year: info.school_year || "",
        signed: Boolean(result.access_token)
      });
      navigate("/home", { replace: true });
    }, ENTER_DELAY);
  }

  async function submitId(e) {
    e.preventDefault();
    if (locked) return;
    if (!studentId.trim() || (needsCode && !accessCode.trim())) {
      setError(vi
        ? "Vui lòng nhập mã sinh viên" + (needsCode ? " và mã truy cập." : ".")
        : "Please enter your student ID" + (needsCode ? " and access code." : "."));
      idInput.current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      const checked = await api.checkStudent({ student_id: studentId.trim(), access_code: accessCode });
      if (!checked.exists) {
        setStudentId(checked.student_id);
        setStep("profile");
        return;
      }
      enter(await api.login({ student_id: checked.student_id, access_code: accessCode }), checked.profile?.name);
    } catch (err) {
      setError(cleanError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitProfile(e) {
    e.preventDefault();
    if (locked) return;
    const name = profile.name.trim();
    const email = profile.email.trim();
    if (name.length < 2) {
      setError(vi ? "Vui lòng nhập họ và tên của bạn." : "Please enter your full name.");
      nameInput.current?.focus();
      return;
    }
    if (email && !EMAIL.test(email)) {
      setError(vi ? "Email chưa đúng định dạng." : "That email doesn't look right.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.login({
        student_id: studentId,
        access_code: accessCode,
        name,
        email,
        major: profile.major.trim(),
        school_year: profile.school_year
      });
      enter(result, name);
    } catch (err) {
      setError(cleanError(err));
    } finally {
      setBusy(false);
    }
  }

  function backToId() {
    if (locked) return;
    setStep("id");
    setError("");
    setProfile(EMPTY_PROFILE);
  }

  const years = SCHOOL_YEARS.map(key => ({ key, label: schoolYearLabel(key, language) }));

  const cardMotion = dir => ({
    initial: { opacity: 0, x: 18 * dir },
    animate: { opacity: zoom ? 0 : 1, x: 0 },
    exit: { opacity: 0, x: 18 * dir },
    transition: { duration: 0.22, ease: "easeOut" }
  });

  const submitLabel = (idle, working) => (
    <>
      {busy && <Loader2 size={18} className="coc-spin" />}
      {zoom
        ? (vi ? `Chào ${welcome}! Đang vào...` : `Hi ${welcome}! Entering...`)
        : busy ? working : idle}
      {!locked && <ArrowRight size={18} />}
    </>
  );

  return (
    <main className={`coc-login-scene ${zoom ? "coc-zooming" : ""}`}>
      <div className="coc-room-background" aria-hidden="true" />
      <div className="coc-room-light" aria-hidden="true" />

      <div className="coc-ambient-effects" aria-hidden="true">
        <div className="coc-cloud coc-cloud-one" />
        <div className="coc-cloud coc-cloud-two" />
        <div className="coc-cloud coc-cloud-three" />

        <div className="coc-plant-animation coc-plant-left">
          <span className="coc-plant-leaf leaf-one" />
          <span className="coc-plant-leaf leaf-two" />
          <span className="coc-plant-leaf leaf-three" />
        </div>

        <div className="coc-plant-animation coc-plant-right">
          <span className="coc-plant-leaf leaf-one" />
          <span className="coc-plant-leaf leaf-two" />
          <span className="coc-plant-leaf leaf-three" />
        </div>
      </div>

      <div className="coc-desk-scene" aria-hidden="true">
        <div className="coc-desk-surface" />
      </div>

      <header className="coc-brand">
        <img src="/images/coc-logo-transparent.png" alt="Logo Cóc Đi Làm" className="coc-brand-logo" />
        <div className="coc-brand-name">
          <h1>Cóc Đi Làm</h1>
          <p>{vi ? "Phòng huấn luyện phỏng vấn ảo" : "Virtual Interview Training"}</p>
        </div>
      </header>

      <div className="coc-language">
        <button type="button" disabled={zoom} onClick={changeLanguage} aria-label="Change language">
          <Languages size={17} />
          {vi ? "Tiếng Việt" : "English"}
        </button>
      </div>

      <div className="coc-laptop-stage">
        <div className="coc-laptop">
          <div className="coc-laptop-top">
            <span className="coc-webcam" />

            <div className="coc-laptop-screen">
              <div className="coc-screen-wallpaper" />

              <AnimatePresence mode="wait" initial={false}>
                {step === "id" ? (
                  <motion.form key="id" onSubmit={submitId} className="coc-login-card" noValidate {...cardMotion(-1)}>
                    <div className="coc-card-logo">
                      <img src="/images/coc-logo-transparent.png" alt="" />
                    </div>

                    <h2>
                      {zoom && welcome
                        ? (vi ? `Chào mừng trở lại, ${welcome}!` : `Welcome back, ${welcome}!`)
                        : (vi ? "Chào mừng bạn!" : "Welcome!")}
                    </h2>
                    <p className="coc-card-subtitle">
                      {vi ? "Nhập mã sinh viên để bắt đầu luyện phỏng vấn" : "Enter your student ID to start practicing"}
                    </p>

                    <label className="coc-input-field">
                      <IdCard size={18} />
                      <input
                        ref={idInput}
                        type="text"
                        autoComplete="username"
                        autoCapitalize="characters"
                        spellCheck={false}
                        aria-label={vi ? "Mã sinh viên" : "Student ID"}
                        placeholder={vi ? "Mã sinh viên (VD: SE123456)" : "Student ID (e.g. SE123456)"}
                        maxLength={64}
                        value={studentId}
                        disabled={locked}
                        onChange={e => { setStudentId(e.target.value); setError(""); }}
                      />
                    </label>

                    {needsCode && (
                      <label className="coc-input-field">
                        <LockKeyhole size={18} />
                        <input
                          type={show ? "text" : "password"}
                          autoComplete="off"
                          aria-label={vi ? "Mã truy cập" : "Access code"}
                          placeholder={vi ? "Mã truy cập của lớp" : "Class access code"}
                          value={accessCode}
                          disabled={locked}
                          onChange={e => { setAccessCode(e.target.value); setError(""); }}
                        />
                        <button
                          type="button"
                          disabled={locked}
                          aria-label={show ? (vi ? "Ẩn mã" : "Hide code") : (vi ? "Hiện mã" : "Show code")}
                          onClick={() => setShow(value => !value)}
                        >
                          {show ? <EyeOff size={18} /> : <Eye size={18} />}
                        </button>
                      </label>
                    )}

                    {error && <p className="coc-error" role="alert">{error}</p>}

                    <button type="submit" disabled={locked} className="coc-login-button">
                      {submitLabel(vi ? "Tiếp tục" : "Continue", vi ? "Đang kiểm tra..." : "Checking...")}
                    </button>

                    <p className="coc-hint">
                      {needsCode
                        ? (vi ? "Chưa có mã truy cập? Hãy hỏi giảng viên của lớp." : "No access code? Ask your instructor.")
                        : (vi ? "Lần đầu đăng nhập? Bạn sẽ được hỏi thêm vài thông tin cơ bản."
                              : "First time here? We'll ask for a few basic details.")}
                    </p>
                  </motion.form>
                ) : (
                  <motion.form
                    key="profile"
                    onSubmit={submitProfile}
                    className="coc-login-card coc-profile-card"
                    noValidate
                    {...cardMotion(1)}
                  >
                    <div className="coc-profile-head">
                      <button
                        type="button"
                        className="coc-back"
                        onClick={backToId}
                        disabled={locked}
                        aria-label={vi ? "Đổi mã sinh viên" : "Change student ID"}
                        title={vi ? "Đổi mã sinh viên" : "Change student ID"}
                      >
                        <ArrowLeft size={17} />
                      </button>
                      <div>
                        <h2>{vi ? "Tạo hồ sơ sinh viên" : "Create your profile"}</h2>
                        <p className="coc-card-subtitle">
                          {vi ? "Mã " : "ID "}
                          <strong className="coc-id-chip">{studentId.toUpperCase()}</strong>
                          {vi ? " chưa có trong hệ thống, điền một lần là xong." : " is new here, fill this in once."}
                        </p>
                      </div>
                    </div>

                    <div className="coc-profile-grid">
                      <label className="coc-input-field coc-span-2">
                        <UserRound size={18} />
                        <input
                          ref={nameInput}
                          type="text"
                          autoComplete="name"
                          maxLength={120}
                          aria-label={vi ? "Họ và tên" : "Full name"}
                          placeholder={vi ? "Họ và tên *" : "Full name *"}
                          value={profile.name}
                          disabled={locked}
                          onChange={e => setField("name", e.target.value)}
                        />
                      </label>

                      <label className="coc-input-field coc-span-2">
                        <Mail size={18} />
                        <input
                          type="email"
                          autoComplete="email"
                          maxLength={160}
                          aria-label="Email"
                          placeholder={vi ? "Email (không bắt buộc)" : "Email (optional)"}
                          value={profile.email}
                          disabled={locked}
                          onChange={e => setField("email", e.target.value)}
                        />
                      </label>

                      <label className="coc-input-field">
                        <GraduationCap size={18} />
                        <input
                          type="text"
                          maxLength={120}
                          aria-label={vi ? "Ngành học" : "Major"}
                          placeholder={vi ? "Ngành học" : "Major"}
                          value={profile.major}
                          disabled={locked}
                          onChange={e => setField("major", e.target.value)}
                        />
                      </label>

                      <label className="coc-input-field">
                        <CalendarRange size={18} />
                        <select
                          aria-label={vi ? "Năm học" : "Year"}
                          value={profile.school_year}
                          disabled={locked}
                          onChange={e => setField("school_year", e.target.value)}
                        >
                          <option value="">{vi ? "Năm học" : "Year"}</option>
                          {years.map(year => <option key={year.key} value={year.key}>{year.label}</option>)}
                        </select>
                      </label>
                    </div>

                    {error && <p className="coc-error" role="alert">{error}</p>}

                    <button type="submit" disabled={locked} className="coc-login-button">
                      {submitLabel(
                        vi ? "Tạo hồ sơ & bắt đầu" : "Create profile & start",
                        vi ? "Đang tạo hồ sơ..." : "Creating profile..."
                      )}
                    </button>
                  </motion.form>
                )}
              </AnimatePresence>
            </div>
          </div>

          <div className="coc-laptop-bottom" />
        </div>
      </div>

      <div className="coc-login-footer">
        <BookOpen size={15} />
        <span>{vi ? "Cóc Đi Làm · Luyện tập hôm nay, tự tin ngày mai" : "Cóc Đi Làm · Practice today, succeed tomorrow"}</span>
      </div>
    </main>
  );
}
