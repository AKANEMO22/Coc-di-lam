
import React, {
  createContext,
  useContext,
  useEffect,
  useState
} from "react";

const LanguageContext = createContext(null);

const translations = {
  vi: {
    brand: "Cóc Đi Làm",
    home: "Trang chủ",
    meetings: "Phỏng vấn",
    results: "Kết quả",
    logout: "Đăng xuất",
    greeting: "Chào mừng trở lại!",
    subtitle: "Sẵn sàng chinh phục buổi phỏng vấn tiếp theo?",
    start: "Bắt đầu phỏng vấn",
    newInterview: "Phỏng vấn mới",
    history: "Lịch sử phỏng vấn",
    recent: "Kết quả gần đây",
    schedule: "Lịch phỏng vấn",
    role: "Vị trí ứng tuyển",
    rolePlaceholder: "Ví dụ: Data Analyst",
    cancel: "Hủy",
    confirm: "Xác nhận",
    today: "Hôm nay",
    completed: "Hoàn thành",
    all: "Tất cả",
    inProgress: "Đang thực hiện",
    score: "Điểm tổng thể",
    skills: "Đánh giá kỹ năng",
    strengths: "Điểm mạnh",
    improvements: "Cần cải thiện",
    feedback: "Nhận xét",
    transcript: "Nội dung phỏng vấn",
    download: "Tải báo cáo",
    search: "Tìm kiếm cuộc phỏng vấn...",
    noResults: "Không tìm thấy kết quả phù hợp.",
    questions: "Câu hỏi",
    answer: "Nhập câu trả lời của bạn...",
    submit: "Gửi câu trả lời",
    end: "Kết thúc phỏng vấn",
    finishTitle: "Kết thúc buổi phỏng vấn?",
    finishDescription:
      "Câu trả lời sẽ được lưu để bạn xem lại sau.",
    continue: "Tiếp tục luyện tập",
    finish: "Kết thúc và xem kết quả",
    saved: "câu trả lời đã lưu",
    hideTranscript: "Ẩn hội thoại",
    showTranscript: "Hiện hội thoại",
    interviewer: "Người phỏng vấn AI",
    you: "Bạn",
    demo: "Chế độ mô phỏng",
    demoNotice:
      "Điểm số và nhận xét hiện được mô phỏng, chưa phải đánh giá AI thực tế.",
    minutes: "phút"
  },
  en: {
    brand: "Cóc Đi Làm",
    home: "Home",
    meetings: "Meetings",
    results: "Results",
    logout: "Sign out",
    greeting: "Welcome back!",
    subtitle: "Ready for your next interview?",
    start: "Start Interview",
    newInterview: "New Interview",
    history: "Interview History",
    recent: "Recent Results",
    schedule: "Interview Schedule",
    role: "Interview Role",
    rolePlaceholder: "e.g. Data Analyst",
    cancel: "Cancel",
    confirm: "Confirm",
    today: "Today",
    completed: "Completed",
    all: "All",
    inProgress: "In Progress",
    score: "Overall Score",
    skills: "Skill Breakdown",
    strengths: "Strengths",
    improvements: "Areas for Improvement",
    feedback: "Feedback",
    transcript: "Interview Transcript",
    download: "Download Report",
    search: "Search interviews...",
    noResults: "No matching results found.",
    questions: "Question",
    answer: "Type your answer...",
    submit: "Submit Answer",
    end: "End Interview",
    finishTitle: "Finish interview?",
    finishDescription:
      "Your answers will be saved for later review.",
    continue: "Continue Practicing",
    finish: "Finish & View Results",
    saved: "answers saved",
    hideTranscript: "Hide Transcript",
    showTranscript: "Show Transcript",
    interviewer: "AI Interviewer",
    you: "You",
    demo: "Demo Mode",
    demoNotice:
      "Scores and feedback are simulated, not real AI assessments.",
    minutes: "minutes"
  }
};

export function LanguageProvider({ children }) {
  const [language, setLanguageState] = useState(() => {
    try {
      return localStorage.getItem("coc-language") === "en"
        ? "en"
        : "vi";
    } catch {
      return "vi";
    }
  });

  useEffect(() => {
    localStorage.setItem("coc-language", language);
    document.documentElement.lang = language;
  }, [language]);

  function setLanguage(value) {
    if (value === "vi" || value === "en") {
      setLanguageState(value);
    }
  }

  function t(key) {
    return translations[language][key] ?? key;
  }

  return (
    <LanguageContext.Provider
      value={{ language, setLanguage, t }}
    >
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error(
      "useLanguage must be used inside LanguageProvider"
    );
  }
  return context;
}
