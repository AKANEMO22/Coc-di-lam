
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Flame,
  Video,
  Plus,
  Clock3,
  FileText,
  ArrowRight,
  CheckCircle2,
  CalendarX2,
  Trophy,
  ArrowUpRight
} from "lucide-react";

import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { useApp } from "../context/AppContext";
import { useLanguage } from "../context/LanguageContext";

import "./Home.css";

function dateKey(value) {
  if (!value) return "";

  // Preserve the calendar date for ISO strings without timezone.
  if (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(value)
  ) {
    return value;
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return "";

  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0")
  ].join("-");
}

function parseKey(key) {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function shiftDay(key, offset) {
  const date = parseKey(key);
  date.setDate(date.getDate() + offset);
  return dateKey(date);
}

function calculateStreak(keys, todayKey) {
  const practiced = new Set(keys);

  let cursor = todayKey;

  // A streak remains active if yesterday was practiced.
  if (!practiced.has(cursor)) {
    cursor = shiftDay(cursor, -1);
  }

  let count = 0;

  while (practiced.has(cursor)) {
    count++;
    cursor = shiftDay(cursor, -1);
  }

  return count;
}

export default function Home() {
  const navigate = useNavigate();
  const { results = [], refreshSessions } = useApp();
  useEffect(() => { void refreshSessions(); }, [refreshSessions]);
  const { language } = useLanguage();

  const vi = language === "vi";

  const [selectedDate, setSelectedDate] = useState(
    () => dateKey(new Date())
  );

  const [visibleMonth, setVisibleMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const [search, setSearch] = useState("");

  const todayKey = dateKey(new Date());

  const completed = useMemo(
    () => results.filter(result =>
      result &&
      result.status !== "In Progress" &&
      result.status !== "Cancelled" &&
      result.status !== "Scheduled" &&
      result.status !== "Pending"
    ),
    [results]
  );

  const interviewsByDate = useMemo(() => {
    const map = {};

    for (const result of completed) {
      const key = dateKey(result.date || result.startedAt);

      if (!key) continue;

      if (!map[key]) map[key] = [];
      map[key].push(result);
    }

    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => {
        const timeA = new Date(a.date || a.startedAt).getTime();
        const timeB = new Date(b.date || b.startedAt).getTime();
        return (timeB || 0) - (timeA || 0);
      });
    }

    return map;
  }, [completed]);

  const practicedDates = Object.keys(interviewsByDate);
  const streak = calculateStreak(practicedDates, todayKey);

  const selectedInterviews = (interviewsByDate[selectedDate] || [])
    .filter(result => {
      const text = [
        result.role,
        result.cvName,
        result.company
      ].join(" ").toLowerCase();

      return text.includes(search.toLowerCase());
    });

  const totalToday = (interviewsByDate[todayKey] || []).length;

  const isToday = selectedDate === todayKey;
  const isFuture = selectedDate > todayKey;

  const firstDay = new Date(
    visibleMonth.getFullYear(),
    visibleMonth.getMonth(),
    1
  );

  const offset = (firstDay.getDay() + 6) % 7;

  const calendarDays = Array.from({ length: 42 }, (_, index) => {
    const day = new Date(
      visibleMonth.getFullYear(),
      visibleMonth.getMonth(),
      index - offset + 1
    );

    return {
      date: day,
      key: dateKey(day),
      inMonth: day.getMonth() === visibleMonth.getMonth()
    };
  });

  const monthTitle = visibleMonth.toLocaleDateString(
    vi ? "vi-VN" : "en-US",
    { month: "long", year: "numeric" }
  );

  const selectedDateTitle = parseKey(selectedDate).toLocaleDateString(
    vi ? "vi-VN" : "en-US",
    {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric"
    }
  );

  function changeMonth(offsetMonths) {
    setVisibleMonth(previous => new Date(
      previous.getFullYear(),
      previous.getMonth() + offsetMonths,
      1
    ));
  }

  function selectDay(day) {
    setSelectedDate(day.key);

    if (!day.inMonth) {
      setVisibleMonth(new Date(
        day.date.getFullYear(),
        day.date.getMonth(),
        1
      ));
    }
  }

  function returnToday() {
    const now = new Date();
    setSelectedDate(dateKey(now));
    setVisibleMonth(
      new Date(now.getFullYear(), now.getMonth(), 1)
    );
  }

  function openResult(id) {
    navigate(
      "/results?selected=" + encodeURIComponent(id)
    );
  }

  function startInterview() {
    navigate("/prepare-interview");
  }

  return (
    <main className="workspace-shell viewport-fit-shell viewport-fit-home">
      <div className="scenery" />

      <Sidebar />

      <section className="workspace-content viewport-fit-content viewport-fit-home-content">
        <Topbar search={search} onSearch={setSearch} />

        <div className="coc-home">
          <header className="coc-home-header">
            <div className="coc-home-welcome">
              <span className="coc-home-eyebrow">
                CÓC ĐI LÀM · INTERVIEW WORKSPACE
              </span>

              <h1>
                {vi
                  ? "Chào mừng trở lại!"
                  : "Welcome back!"}
              </h1>

              <p>
                {vi
                  ? "Theo dõi tiến độ và duy trì thói quen luyện phỏng vấn."
                  : "Track your progress and build a daily interview habit."}
              </p>
            </div>

            <div className="coc-home-widgets">
              <section className="coc-calendar glass">
                <div className="coc-calendar-header">
                  <strong>{monthTitle}</strong>

                  <div className="coc-calendar-navigation">
                    <button
                      onClick={() => changeMonth(-1)}
                      aria-label="Previous month"
                    >
                      <ChevronLeft size={17} />
                    </button>

                    <button
                      onClick={returnToday}
                      title={vi ? "Hôm nay" : "Today"}
                      aria-label={vi ? "Hôm nay" : "Today"}
                    >
                      <CalendarDays size={17} />
                    </button>

                    <button
                      onClick={() => changeMonth(1)}
                      aria-label="Next month"
                    >
                      <ChevronRight size={17} />
                    </button>
                  </div>
                </div>

                <div className="coc-calendar-grid">
                  {(vi
                    ? ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
                    : ["M", "T", "W", "T", "F", "S", "S"]
                  ).map((day, index) => (
                    <span
                      className="coc-calendar-weekday"
                      key={index}
                    >
                      {day}
                    </span>
                  ))}

                  {calendarDays.map(day => {
                    const hasInterview =
                      (interviewsByDate[day.key] || []).length > 0;

                    const selected = day.key === selectedDate;
                    const today = day.key === todayKey;

                    return (
                      <button
                        key={day.key}
                        className={[
                          "coc-calendar-day",
                          day.inMonth ? "" : "outside",
                          selected ? "selected" : "",
                          today ? "today" : ""
                        ].join(" ")}
                        onClick={() => selectDay(day)}
                        title={
                          hasInterview
                            ? `${interviewsByDate[day.key].length} ${
                                vi ? "buổi phỏng vấn" : "interviews"
                              }`
                            : ""
                        }
                        aria-pressed={selected}
                      >
                        <span>{day.date.getDate()}</span>

                        <i
                          className={
                            hasInterview
                              ? "coc-calendar-dot visible"
                              : "coc-calendar-dot"
                          }
                        />
                      </button>
                    );
                  })}
                </div>

                <div className="coc-calendar-legend">
                  <span className="coc-legend-dot" />
                  {vi ? "Đã luyện tập" : "Practiced"}
                </div>
              </section>

              <section className="coc-streak glass">
                <div className="coc-streak-heading">
                  <span>
                    <Flame size={20} />
                    STREAK
                  </span>
                </div>

                <div className="coc-streak-number">
                  <Flame size={35} />
                  <strong>{streak}</strong>
                </div>

                <b>
                  {vi ? "ngày liên tiếp" : "day streak"}
                </b>

                <p>
                  {streak === 0
                    ? (vi
                        ? "Luyện tập hôm nay để bắt đầu chuỗi!"
                        : "Practice today to start a streak!")
                    : totalToday > 0
                      ? (vi
                          ? "Tuyệt vời! Bạn đã luyện tập hôm nay."
                          : "Great! You've practiced today.")
                      : (vi
                          ? "Luyện tập hôm nay để nối tiếp chuỗi."
                          : "Practice today to continue your streak.")}
                </p>
              </section>
            </div>
          </header>

          <section className="coc-daily-panel glass">
            <div className="coc-daily-panel-header">
              <div>
                <span className="coc-home-eyebrow">
                  {vi
                    ? "NHẬT KÝ LUYỆN TẬP"
                    : "INTERVIEW JOURNAL"}
                </span>

                <h2>
                  {isToday
                    ? (vi ? "Hôm nay" : "Today")
                    : selectedDateTitle}
                </h2>

                <p>
                  {isToday
                    ? (vi
                        ? "Hoạt động luyện phỏng vấn trong ngày"
                        : "Today's interview practice")
                    : (vi
                        ? "Lịch sử luyện tập của ngày đã chọn"
                        : "Practice history for the selected day")}
                </p>
              </div>

              {selectedDate !== todayKey && (
                <button
                  className="coc-today-button"
                  onClick={returnToday}
                >
                  <CalendarDays size={16} />
                  {vi ? "Về hôm nay" : "Today"}
                </button>
              )}
            </div>

            {selectedInterviews.length === 0 ? (
              <div className="coc-daily-empty">
                <div className="coc-daily-empty-icon">
                  {isFuture
                    ? <CalendarDays size={48} />
                    : <CalendarX2 size={48} />}
                </div>

                <h3>
                  {isToday
                    ? (vi
                        ? "Hôm nay bạn chưa luyện phỏng vấn buổi nào!"
                        : "You haven't practiced today!")
                    : isFuture
                      ? (vi
                          ? "Ngày này chưa diễn ra"
                          : "This day is in the future")
                      : (vi
                          ? "Không có buổi phỏng vấn nào trong ngày này"
                          : "No interviews on this day")}
                </h3>

                <p>
                  {isToday
                    ? (vi
                        ? "Dành một chút thời gian luyện tập để tự tin hơn và duy trì streak của bạn."
                        : "Take some time to practice, build confidence and keep your streak going.")
                    : (vi
                        ? "Bạn có thể chọn một ngày khác trên lịch để xem lịch sử."
                        : "Choose another date to view your interview history.")}
                </p>

                {isToday && (
                  <button
                    className="btn btn-primary coc-start-button"
                    onClick={startInterview}
                  >
                    <Plus size={19} />
                    {vi
                      ? "Thêm buổi phỏng vấn"
                      : "Add Interview Session"}
                    <ArrowRight size={18} />
                  </button>
                )}
              </div>
            ) : (
              <div className="coc-daily-history">
                <div className="coc-daily-history-summary">
                  <div>
                    <CheckCircle2 size={22} />
                    <strong>
                      {vi
                        ? `${selectedInterviews.length} buổi phỏng vấn`
                        : `${selectedInterviews.length} interviews`}
                    </strong>
                  </div>

                  {isToday && (
                    <button
                      className="btn btn-primary"
                      onClick={startInterview}
                    >
                      <Plus size={17} />
                      {vi ? "Thêm buổi" : "Add Session"}
                    </button>
                  )}
                </div>

                <div className="coc-daily-session-list">
                  {selectedInterviews.map(result => {
                    const date = new Date(
                      result.date || result.startedAt
                    );

                    const time = Number.isNaN(date.getTime())
                      ? "--:--"
                      : date.toLocaleTimeString(
                          vi ? "vi-VN" : "en-US",
                          {
                            hour: "2-digit",
                            minute: "2-digit"
                          }
                        );
                    const dateLabel = Number.isNaN(date.getTime())
                      ? (vi ? "Ngày không rõ" : "Date unavailable")
                      : date.toLocaleDateString(vi ? "vi-VN" : "en-US", {
                          day: "numeric",
                          month: "short",
                          year: "numeric"
                        });
                    const role = result.role || (vi ? "Phỏng vấn" : "Interview");

                    return (
                      <button
                        className="coc-daily-session"
                        key={result.id}
                        onClick={() => openResult(result.id)}
                        aria-label={vi
                          ? `Xem kết quả ${role}, ${dateLabel} lúc ${time}`
                          : `View report for ${role}, ${dateLabel} at ${time}`}
                      >
                        <div className="coc-session-icon">
                          <Video size={23} />
                        </div>

                        <div className="coc-session-info">
                          <div className="coc-session-heading">
                            <strong>{role}</strong>
                            <span className="coc-session-complete">
                              <CheckCircle2 size={14} />
                              {vi ? "Đã hoàn thành" : "Completed"}
                            </span>
                          </div>

                          <div className="coc-session-meta">
                            <span><CalendarDays size={14} />{dateLabel}</span>
                            <span><Clock3 size={14} />{time}</span>
                          </div>

                          {result.cvName && (
                            <span className="coc-session-cv">
                              <FileText size={14} />
                              {result.cvName}
                            </span>
                          )}
                        </div>

                        <span className="coc-session-action">
                          <span>
                            <small>{vi ? "Báo cáo" : "Report"}</small>
                            <strong>{vi ? "Xem kết quả" : "View report"}</strong>
                          </span>
                          <span className="coc-session-action-icon"><ArrowUpRight size={18} /></span>
                        </span>
                      </button>
                    );
                  })}
                </div>

                <div className="coc-daily-history-footer">
                  <Trophy size={17} />
                  {vi
                    ? "Mỗi buổi phỏng vấn đều là một bước tiến!"
                    : "Every interview is a step forward!"}
                </div>
              </div>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}
