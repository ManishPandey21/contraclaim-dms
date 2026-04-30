import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set
from enum import Enum

logger = logging.getLogger(__name__)

class CategoryKey(str, Enum):
    """Enum for contract category keys to prevent typos and ensure type safety"""
    GENERAL = "general"
    EMPLOYER = "employer"
    ENGINEER = "engineer"
    CONTRACTOR = "contractor"
    NOMINATED = "nominated"
    STAFF = "staff"
    PLANT = "plant"
    COMMENCEMENT = "commencement"
    TESTS = "tests"
    TAKINGOVER = "takingover"
    DEFECTS = "defects"
    MEASUREMENT = "measurement"
    VARIATIONS = "variations"
    PAYMENT = "payment"
    TERMINATION = "termination"
    RISK = "risk"
    INSURANCE = "insurance"
    FORCEMAJEURE = "forcemajeure"
    CLAIMS = "claims"

@dataclass
class CategoryInfo:
    """Data class for category metadata"""
    keywords: List[str]
    description: str

class ContractCategorizerConfig:
    """Configuration class for contract categorizer"""
    MAX_CATEGORIES_FALLBACK = 6
    MAX_TEXT_LENGTH = 12000
    LLM_TEMPERATURE = 0.0
    LLM_MAX_TOKENS = 300
    DEFAULT_MODEL = "gpt-4o-mini"

# Contract categorization taxonomy with type safety
CATEGORY_TAXONOMY: Dict[CategoryKey, CategoryInfo] = {
    CategoryKey.GENERAL: CategoryInfo(
        keywords=["general provisions", "definitions", "interpretation", "law and language", "priority of documents"],
        description="General provisions and definitions"
    ),
    CategoryKey.EMPLOYER: CategoryInfo(
        keywords=["employer", "employer's personnel", "employer's claims", "employer's risks"],
        description="Employer responsibilities and rights"
    ),
    CategoryKey.ENGINEER: CategoryInfo(
        keywords=["engineer", "engineer's duties", "engineer's authority", "engineer's determination"],
        description="Engineer role and authority"
    ),
    CategoryKey.CONTRACTOR: CategoryInfo(
        keywords=["contractor", "contractor's general obligations", "performance security", "contractor's representatives"],
        description="Contractor responsibilities and obligations"
    ),
    CategoryKey.NOMINATED: CategoryInfo(
        keywords=["nominated subcontractors", "objection to nomination", "payments to nominated subcontractors"],
        description="Nominated subcontractors"
    ),
    CategoryKey.STAFF: CategoryInfo(
        keywords=["staff and labour", "rates of wages", "labour conditions", "personnel and equipment records"],
        description="Staff and labor provisions"
    ),
    CategoryKey.PLANT: CategoryInfo(
        keywords=["plant materials and workmanship", "manner of execution", "inspections", "testing"],
        description="Plant, materials and workmanship"
    ),
    CategoryKey.COMMENCEMENT: CategoryInfo(
        keywords=["commencement of works", "time for completion", "programme", "rate of progress"],
        description="Commencement, delays and suspension"
    ),
    CategoryKey.TESTS: CategoryInfo(
        keywords=["tests on completion", "delayed tests", "retesting", "failure to pass tests on completion"],
        description="Tests on completion"
    ),
    CategoryKey.TAKINGOVER: CategoryInfo(
        keywords=["employer's taking over", "taking-over certificate", "interference with tests on completion"],
        description="Employer's taking over"
    ),
    CategoryKey.DEFECTS: CategoryInfo(
        keywords=["defects liability", "completion of outstanding work", "removal of defective work"],
        description="Defects liability"
    ),
    CategoryKey.MEASUREMENT: CategoryInfo(
        keywords=["measurement and evaluation", "works to be measured", "valuation", "omissions"],
        description="Measurement and evaluation"
    ),
    CategoryKey.VARIATIONS: CategoryInfo(
        keywords=["variations and adjustments", "value engineering", "variation procedure", "adjustments for changes in cost"],
        description="Variations and adjustments"
    ),
    CategoryKey.PAYMENT: CategoryInfo(
        keywords=["contract price and payment", "advance payment", "payment certificates", "delayed payment"],
        description="Contract price and payment"
    ),
    CategoryKey.TERMINATION: CategoryInfo(
        keywords=["termination by employer", "termination by contractor", "payment after termination"],
        description="Termination provisions"
    ),
    CategoryKey.RISK: CategoryInfo(
        keywords=["risk and responsibility", "indemnities", "limitation of liability", "intellectual property"],
        description="Risk and responsibility"
    ),
    CategoryKey.INSURANCE: CategoryInfo(
        keywords=["insurance", "insurance for works and contractor's equipment", "insurance against injury to persons and damage to property"],
        description="Insurance provisions"
    ),
    CategoryKey.FORCEMAJEURE: CategoryInfo(
        keywords=["force majeure", "definition of force majeure", "notice of force majeure", "consequences of force majeure"],
        description="Force majeure provisions"
    ),
    CategoryKey.CLAIMS: CategoryInfo(
        keywords=["claims disputes and arbitration", "contractor's claims", "appointment of the dispute board", "arbitration"],
        description="Claims, disputes and arbitration"
    ),
}

class ContractCategorizerError(Exception):
    """Custom exception for contract categorizer errors"""
    pass

class LLMService:
    """Service for handling LLM interactions with proper error handling"""
    
    def __init__(self, settings):
        self.settings = settings
        self._client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize LLM client with proper error handling"""
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.prompts import ChatPromptTemplate
            
            if not self.settings.OPENAI_API_KEY:
                logger.warning("OpenAI API key not configured, LLM categorization disabled")
                return
                
            self._client = ChatOpenAI(
                model=getattr(self.settings, "OPENAI_RESPONSES_MODEL", ContractCategorizerConfig.DEFAULT_MODEL),
                temperature=ContractCategorizerConfig.LLM_TEMPERATURE,
                api_key=self.settings.OPENAI_API_KEY,
                max_tokens=ContractCategorizerConfig.LLM_MAX_TOKENS
            )
            self._prompt_template = ChatPromptTemplate
            logger.info("LLM client initialized successfully")
            
        except ImportError as e:
            logger.warning(f"LangChain not available: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
    
    def is_available(self) -> bool:
        """Check if LLM service is available"""
        return self._client is not None
    
    async def categorize(self, text: str, organization_name: Optional[str], project_name: Optional[str]) -> List[str]:
        """Categorize text using LLM with proper error handling"""
        if not self.is_available():
            return []
        
        try:
            # Truncate text to prevent token limit issues
            truncated_text = text[:ContractCategorizerConfig.MAX_TEXT_LENGTH]
            
            # Build context strings
            org_context = f"Organization: {organization_name}" if organization_name else "Organization: (unknown)"
            proj_context = f"Project: {project_name}" if project_name else "Project: (unknown)"
            
            # Create system prompt
            allowed_keys = [key.value for key in CategoryKey]
            allowed_str = ", ".join(allowed_keys)
            system_prompt = (
                "You are an expert contract analyst. Classify the provided contract excerpt into zero or more of the following "
                f"predefined categories. Only select from these exact keys: {allowed_str}.\n"
                "Return strictly a JSON object with the shape: {{\"categories\": [\"key1\", \"key2\", ...]}} where each key is from the allowed list. "
                "If no category fits, return {{\"categories\": []}}. Do not include any explanations."
            )
            
            # Create user prompt
            user_prompt = f"{org_context}\n{proj_context}\n\nContract excerpt:\n{truncated_text}"
            
            # Create and invoke chain
            prompt = self._prompt_template.from_messages([("system", system_prompt), ("user", user_prompt)])
            chain = prompt | self._client
            
            response = chain.invoke({})
            content = getattr(response, "content", None)
            
            if not content:
                logger.warning("Empty response from LLM")
                return []
            
            # Parse JSON response
            try:
                data = json.loads(content)
                categories = data.get("categories", [])
                
                if not isinstance(categories, list):
                    logger.warning("Invalid categories format from LLM")
                    return []
                
                # Validate and filter categories
                valid_categories = []
                valid_keys = {key.value for key in CategoryKey}
                
                for category in categories:
                    if isinstance(category, str) and category in valid_keys:
                        if category not in valid_categories:  # Avoid duplicates
                            valid_categories.append(category)
                
                logger.info(f"LLM categorization successful: {len(valid_categories)} categories found")
                return valid_categories
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                return []
                
        except Exception as e:
            logger.error(f"LLM categorization failed: {e}")
            return []

class KeywordMatcher:
    """Service for keyword-based categorization with improved matching"""
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for better matching"""
        if not isinstance(text, str):
            raise ValueError("Text must be a string")
        
        normalized = text.lower()
        normalized = re.sub(r'\s+', ' ', normalized)
        normalized = normalized.strip()
        return normalized
    
    @staticmethod
    def match_categories(text: str) -> List[str]:
        """Match categories based on keyword presence"""
        if not text or not text.strip():
            return []
        
        try:
            normalized_text = KeywordMatcher.normalize_text(text)
            category_scores = []
            
            for category_key, category_info in CATEGORY_TAXONOMY.items():
                score = 0
                
                for keyword in category_info.keywords:
                    normalized_keyword = KeywordMatcher.normalize_text(keyword)
                    if normalized_keyword in normalized_text:
                        score += 1
                
                if score > 0:
                    category_scores.append((category_key.value, score))
            
            # Sort by score (descending) and limit results
            category_scores.sort(key=lambda x: x[1], reverse=True)
            top_categories = [key for key, _ in category_scores[:ContractCategorizerConfig.MAX_CATEGORIES_FALLBACK]]
            
            logger.info(f"Keyword matching found {len(top_categories)} categories")
            return top_categories
            
        except Exception as e:
            logger.error(f"Keyword matching failed: {e}")
            return []

class ContractCategorizer:
    """Main contract categorizer service with hybrid approach"""
    
    def __init__(self, settings):
        self.settings = settings
        self.llm_service = LLMService(settings)
        self.keyword_matcher = KeywordMatcher()
    
    def _validate_input(self, text: str) -> None:
        """Validate input parameters"""
        if not isinstance(text, str):
            raise ContractCategorizerError("Text must be a string")
        
        if not text.strip():
            raise ContractCategorizerError("Text cannot be empty")
    
    async def categorize(
        self,
        text: str,
        organization_name: Optional[str] = None,
        project_name: Optional[str] = None,
        prefer_llm: bool = True
    ) -> List[str]:
        """
        Categorize contract text using hybrid approach.
        
        Args:
            text: Contract text to categorize
            organization_name: Optional organization context
            project_name: Optional project context
            prefer_llm: Whether to prefer LLM over keyword matching
            
        Returns:
            List of category keys
            
        Raises:
            ContractCategorizerError: If input validation fails
        """
        try:
            # Validate input
            self._validate_input(text)
            
            categories = []
            
            # Try LLM first if preferred and available
            if prefer_llm and self.llm_service.is_available():
                logger.info("Attempting LLM categorization")
                categories = await self.llm_service.categorize(text, organization_name, project_name)
            
            # Fallback to keyword matching if LLM failed or not preferred
            if not categories:
                logger.info("Using keyword-based categorization")
                categories = self.keyword_matcher.match_categories(text)
            
            # Ensure uniqueness and validity
            unique_categories = []
            valid_keys = {key.value for key in CategoryKey}
            
            for category in categories:
                if category in valid_keys and category not in unique_categories:
                    unique_categories.append(category)
            
            logger.info(f"Final categorization result: {len(unique_categories)} categories")
            return unique_categories
            
        except ContractCategorizerError:
            raise
        except Exception as e:
            logger.error(f"Categorization failed: {e}")
            raise ContractCategorizerError(f"Categorization failed: {str(e)}")

# Factory function for creating categorizer instance
def create_contract_categorizer(settings) -> ContractCategorizer:
    """Factory function to create contract categorizer instance"""
    return ContractCategorizer(settings)

# Convenience function to maintain backward compatibility
async def categorize_contract(
    text: str,
    organization_name: Optional[str] = None,
    project_name: Optional[str] = None,
    prefer_llm: bool = True,
    settings=None
) -> List[str]:
    """
    Convenience function for contract categorization.
    
    Note: In production, you should create a categorizer instance once and reuse it.
    """
    if settings is None:
        from ..core.config import settings
    
    categorizer = create_contract_categorizer(settings)
    return await categorizer.categorize(text, organization_name, project_name, prefer_llm)
