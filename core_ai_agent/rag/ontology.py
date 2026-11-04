import hashlib
import re
import unicodedata

ONTOLOGY_VERSION = "roleup-ontology-4"
LEVELS = ("Intern", "Fresher", "Junior")


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"\s+", " ", text)


ALIASES = {
    "embeddings": "embedding",
    "embedding vectors": "embedding",
    "vector databases": "vector database",
    "vector search": "retrieval",
    "semantic search": "retrieval",
    "rag": "retrieval augmented generation",
    "retrieval-augmented generation": "retrieval augmented generation",
    "vector db": "vector database",
    "vectordb": "vector database",
    "llm": "large language model",
    "llms": "large language model",
    "jwt": "json web token",
    "js": "javascript",
    "reactjs": "react",
    "react.js": "react",
    "spring": "spring boot",
    "postgres": "postgresql",
    "nodejs": "node.js",
    "node js": "node.js",
    "tf": "tensorflow",
    "sklearn": "scikit-learn",
    "k8s": "kubernetes",
    "ci/cd": "continuous integration",
    "cicd": "continuous integration",
}

SKILL_CONCEPTS = {
    "python", "java", "javascript", "typescript", "c", "c++", "c#", "go",
    "golang", "php", "ruby", "kotlin", "swift", "scala", "r", "sql",
    "html", "css", "node.js", "django", "fastapi", "flask", "spring boot",
    "react", "vue", "angular", "next.js", "postgresql", "mysql", "sqlite",
    "mongodb", "redis", "elasticsearch", "kafka", "spark", "airflow", "dbt",
    "docker", "kubernetes", "terraform", "aws", "azure", "gcp", "linux",
    "git", "github actions", "jenkins", "pytorch", "tensorflow", "scikit-learn",
    "pandas", "numpy", "machine learning", "deep learning", "natural language processing",
    "computer vision", "large language model", "retrieval augmented generation",
    "vector database", "postgresql", "graphql", "rest api", "grpc", "microservices",
    "system design", "unit testing", "integration testing", "continuous integration",
}


def canonical(label: str) -> str:
    label = normalized(label)
    return ALIASES.get(label, label)


def entity_id(label: str) -> str:
    return "concept:" + hashlib.sha256(canonical(label).encode()).hexdigest()[:20]


def tokens(text: str) -> list[str]:
    return re.findall(r"[\w]+(?:[.+#/-][\w+#]+)*|c\+\+|c#|\.net", normalized(text))


def mentions(text: str, label: str) -> bool:
    text = normalized(text)
    label = canonical(label)
    variants = {label} | {a for a, b in ALIASES.items() if b == label}
    return any(re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text) for v in variants)


# Rule-defined concept relations. They are inspectable seeds, not human-certified facts.
# Every edge is attributed to this ontology version and flagged rule_defined.
RELATIONS = [
    ("retrieval augmented generation", "HAS_COMPONENT", "retrieval"),
    ("retrieval augmented generation", "HAS_COMPONENT", "vector database"),
    ("retrieval augmented generation", "HAS_COMPONENT", "embedding"),
    ("retrieval augmented generation", "EVALUATED_BY", "faithfulness"),
    ("retrieval", "EVALUATED_BY", "recall"),
    ("retrieval", "EVALUATED_BY", "precision"),
    ("vector database", "USES_CONCEPT", "embedding"),
    ("large language model", "HAS_RISK", "hallucination"),
    ("pytorch", "USES_CONCEPT", "deep learning"),
    ("deep learning", "HAS_PREREQUISITE", "machine learning"),
    ("spring boot", "USES_CONCEPT", "dependency injection"),
    ("spring boot", "HAS_PREREQUISITE", "java"),
    ("react", "USES_CONCEPT", "virtual dom"),
    ("react", "USES_CONCEPT", "hooks"),
    ("react", "HAS_PREREQUISITE", "javascript"),
    ("javascript", "USES_CONCEPT", "event loop"),
    ("sql", "USES_CONCEPT", "index"),
    ("sql", "USES_CONCEPT", "transaction"),
    ("docker", "USES_CONCEPT", "container"),
    ("kubernetes", "USES_CONCEPT", "container"),
    ("json web token", "USES_CONCEPT", "authentication"),
]

ROLE_PATTERNS = [
    ("IF_AGENTIC_AI", r"agentic|ai agent|ai tc t"),
    ("IF_AI_SWE", r"ai software|llm|\brag\b|genai|forward deployed engineer|fde.*ai"),
    ("IF_DS", r"data scientist|data science|ai scientist"),
    ("IF_DBA_DWE", r"oracle|dba|database admin|data warehouse|dwe|data lake|data modeler|trading system|csdl"),
    ("IF_DE", r"data engineer|\betl\b|database engineer"),
    ("IF_DA", r"data analyst|business intelligence|power bi|powerbi|tableau|data analytics|phân tích dữ liệu"),
    ("IF_AI", r"ai engineer|ai expert|ai developer|machine learning|deep learning|tr tu nhn to|lập trình ai|ai product"),
    ("IF_AUTO_QA", r"automation.*(?:qa|test|qc)|(?:qa|test|qc).*automation|automation engineer"),
    ("IF_QA_QC", r"\bqa\b|\bqc\b|tester|testing|kim th|quality control|checker"),
    ("IF_SECURITY", r"security|an ton thng tin|an ninh mng|devsecops|cyber.*security|iam|access control|rủi ro mảng cntt"),
    ("IF_CLOUD_SA", r"cloud platform|cloud presales|solutions architect|solution architect|enterprise architect|cloud architect"),
    ("IF_DEVOPS", r"devops|\bsre\b|cloud engineer|platform engineer"),
    ("IF_NETWORK", r"network|sysadmin|system engineer|infrastructure|h thng mng|qun tr h thng|vmware|linux admin|windows server|viễn thông|ran architecture|transmission"),
    ("IF_BRSE", r"brse|bridge system engineer|bridge engineer|k s cu ni"),
    ("IF_PO_PM", r"product owner|\bpo\b|project manager|\bpm\b|pmo|game producer|qun l d n|qun tr sn phm|giám đốc sản phẩm|head of product"),
    ("IF_TECH_LEAD", r"technical lead|tech lead|dev lead|team lead|trưởng nhóm.*kỹ thuật|trưởng nhóm.*phần mềm|trưởng nhóm|engineering manager|it manager|center director|solution delivery lead|trưởng phòng.*phần mềm|trưởng phòng.*it"),
    ("IF_EMBEDDED", r"embedded|firmware|autosar|vi iu khin|pcb layout|fpga|mạch điện"),
    ("IF_UNITY", r"unity|game developer|game engineer|rigger|modeler|artist"),
    ("IF_MOBILE_IOS", r"\bios\b|swift"),
    ("IF_MOBILE_ANDROID", r"android|kotlin|flutter|mobile developer"),
    ("IF_ERP", r"\berp\b|odoo|\bsap\b|\bmes\b|functional consultant"),
    ("IF_PRODUCT_ENG", r"product engineer|growth engineer|product operations"),
    ("IF_BA", r"business analyst|phn tch nghip v|\bba\b"),
    ("IF_FULLSTACK", r"full[ -]?stack"),
    ("IF_BE_JAVA", r"\bjava\b|spring"),
    ("IF_BE_NET", r"\.net|c#|dotnet"),
    ("IF_BE_PHP", r"\bphp\b|laravel"),
    ("IF_FE", r"front[ -]?end|react|vue|angular|web content"),
    ("IF_SWE", r"software engineer|k s phn mm|lead golang|golang engineer"),
    ("IF_BE_GEN", r"back[ -]?end|python|django|fastapi|node\.?js|golang|api integration"),
    ("IF_SWD", r"software developer|lp trnh vin|chuyên viên phát triển|chuyên viên ứng dụng|developer|lập trình|phát triển hệ thống"),
]


def resolve_role(text: str, framework_ids: set[str], skills: list[str] | None = None) -> str | None:
    norm_text = normalized(text)
    for role, pattern in ROLE_PATTERNS:
        if role in framework_ids and re.search(pattern, norm_text):
            return role
    if skills:
        s_text = normalized(" ".join(skills))
        for role, pattern in ROLE_PATTERNS:
            if role in framework_ids and re.search(pattern, s_text):
                return role
    tech_signals = r"engineer|developer|specialist|analyst|architect|lead|manager|kỹ sư|chuyên viên|lập trình|phần mềm|hệ thống|cntt|\bit\b"
    if re.search(tech_signals, norm_text):
        if "IF_SWE" in framework_ids:
            return "IF_SWE"
        if "IF_SWD" in framework_ids:
            return "IF_SWD"
    return None


def resolve_level(text: str) -> str | None:
    value = normalized(text)
    # Never silently turn a Senior-only JD into a Fresher interview.
    for level in reversed(LEVELS):
        if normalized(level) in value:
            return level
    if "thực tập" in value or "internship" in value:
        return "Intern"
    if "mới tốt nghiệp" in value or "entry level" in value:
        return "Fresher"
    return None
