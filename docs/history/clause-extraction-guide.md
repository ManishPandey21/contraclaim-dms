# Complete Clause Extraction Implementation Guide

## Problem Analysis

Your current contract processing system splits documents into arbitrary chunks of ~4000 characters, which:
- **Breaks clauses mid-content** - Users see incomplete text
- **Loses context** - No clause headers or identification
- **Creates poor UX** - Fragmented, hard-to-read results
- **Misses structure** - Ignores legal document organization

## Solution Overview

Implement **intelligent clause extraction** that:
1. **Identifies complete clauses** using pattern matching
2. **Preserves clause headers and titles**
3. **Stores clause metadata** (number, title, type, hierarchy)
4. **Intelligently splits** only very long clauses at natural boundaries
5. **Groups chunks** in frontend to show complete clauses

---

## Implementation Steps

### Phase 1: Backend - Clause Extraction (contracts_ingest.py)

#### Step 1: Add ClauseInfo Dataclass

At the top of `contracts_ingest.py`, add:

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ClauseInfo:
    """Represents a complete contract clause"""
    clause_number: str  # e.g., "1.2.3" or "Article 5"
    clause_title: str   # e.g., "Payment Terms"
    clause_text: str    # Full text of the clause
    clause_type: str    # "section", "clause", "article", "paragraph"
    start_position: int # Character position in document
    end_position: int   # Character position in document
    level: int          # Hierarchy level (1 for main, 2 for sub, etc.)
    parent_number: Optional[str] = None  # Parent clause number if nested
```

#### Step 2: Add ClauseExtractor Class

Insert after the `TextChunker` class:

```python
class ClauseExtractor:
    """Enhanced clause extraction that preserves complete clauses"""
    
    # Comprehensive patterns for legal document structures
    CLAUSE_PATTERNS = [
        # "CLAUSE 1.2.3 - Title" or "CLAUSE 1.2.3: Title"
        r'^\s*(CLAUSE|SECTION|ARTICLE)\s+([\d\.]+)\s*[-:]?\s*(.*)$',
        # "1.2.3 Title" (numbered with title)
        r'^\s*(\d+(?:\.\d+){0,3})\s+([A-Z][\w\s,]+)\s*$',
        # "1.2.3. Title" (with period)
        r'^\s*(\d+(?:\.\d+){0,3})\.\s+([A-Z][\w\s,]+)\s*$',
        # Standalone numbered clause
        r'^\s*(\d+(?:\.\d+){0,3})\s*$',
    ]
    
    def __init__(self):
        self.patterns = [re.compile(p, re.MULTILINE | re.IGNORECASE) for p in self.CLAUSE_PATTERNS]
    
    def extract_clauses(self, text: str) -> List[ClauseInfo]:
        """
        Extract complete clauses from contract text.
        Returns list of ClauseInfo objects with complete clause content.
        """
        if not text or not text.strip():
            return []
        
        # Normalize line endings
        normalized_text = text.replace('\r\n', '\n').replace('\r', '\n')
        lines = normalized_text.split('\n')
        
        clause_markers = []  # List of (line_idx, clause_number, clause_title, clause_type)
        
        # First pass: identify all clause headers
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            
            for pattern in self.patterns:
                match = pattern.match(line)
                if match:
                    groups = match.groups()
                    if len(groups) >= 2:
                        clause_type = groups[0] if groups[0].upper() in ['CLAUSE', 'SECTION', 'ARTICLE'] else 'clause'
                        clause_number = groups[1] if len(groups) > 1 else groups[0]
                        clause_title = groups[2].strip() if len(groups) > 2 and groups[2] else ""
                    else:
                        clause_type = 'clause'
                        clause_number = groups[0]
                        clause_title = ""
                    
                    clause_markers.append((idx, clause_number, clause_title, clause_type))
                    break
        
        if not clause_markers:
            # No clauses found, treat entire text as single section
            return [ClauseInfo(
                clause_number="1",
                clause_title="Complete Document",
                clause_text=text,
                clause_type="document",
                start_position=0,
                end_position=len(text),
                level=1
            )]
        
        # Second pass: extract complete clause content
        clauses = []
        
        for i, (line_idx, clause_number, clause_title, clause_type) in enumerate(clause_markers):
            # Find start position
            start_line = line_idx
            start_pos = sum(len(lines[j]) + 1 for j in range(start_line))  # +1 for newline
            
            # Find end position (start of next clause or end of document)
            if i < len(clause_markers) - 1:
                end_line = clause_markers[i + 1][0]
            else:
                end_line = len(lines)
            
            # Extract complete clause text
            clause_lines = lines[start_line:end_line]
            clause_text = '\n'.join(clause_lines).strip()
            
            # Calculate hierarchy level based on clause numbering
            level = clause_number.count('.') + 1 if '.' in clause_number else 1
            
            # Determine parent clause number
            parent_number = None
            if '.' in clause_number:
                parent_number = '.'.join(clause_number.split('.')[:-1])
            
            clause_info = ClauseInfo(
                clause_number=clause_number,
                clause_title=clause_title or f"{clause_type.title()} {clause_number}",
                clause_text=clause_text,
                clause_type=clause_type.lower(),
                start_position=start_pos,
                end_position=start_pos + len(clause_text),
                level=level,
                parent_number=parent_number
            )
            
            clauses.append(clause_info)
        
        return clauses
    
    def split_long_clause(self, clause: ClauseInfo, max_length: int = 6000) -> List[Dict[str, Any]]:
        """
        Split a long clause intelligently at paragraph or sentence boundaries.
        Returns list of clause chunks that maintain context.
        """
        if len(clause.clause_text) <= max_length:
            return [{
                'text': clause.clause_text,
                'clause_number': clause.clause_number,
                'clause_title': clause.clause_title,
                'chunk_index': 0,
                'is_complete': True
            }]
        
        # Split at paragraph boundaries first
        paragraphs = clause.clause_text.split('\n\n')
        chunks = []
        current_chunk = []
        current_length = 0
        chunk_index = 0
        
        header = f"{clause.clause_type.upper()} {clause.clause_number}"
        if clause.clause_title:
            header += f": {clause.clause_title}"
        header += "\n\n"
        
        for para in paragraphs:
            para_len = len(para)
            
            # If single paragraph exceeds max_length, split at sentences
            if para_len > max_length:
                sentences = re.split(r'([.!?]\s+)', para)
                for sent in sentences:
                    if current_length + len(sent) > max_length and current_chunk:
                        chunk_text = header + '\n\n'.join(current_chunk)
                        chunks.append({
                            'text': chunk_text,
                            'clause_number': clause.clause_number,
                            'clause_title': clause.clause_title,
                            'chunk_index': chunk_index,
                            'is_complete': False
                        })
                        current_chunk = []
                        current_length = len(header)
                        chunk_index += 1
                    
                    current_chunk.append(sent)
                    current_length += len(sent)
            
            elif current_length + para_len > max_length and current_chunk:
                chunk_text = header + '\n\n'.join(current_chunk)
                chunks.append({
                    'text': chunk_text,
                    'clause_number': clause.clause_number,
                    'clause_title': clause.clause_title,
                    'chunk_index': chunk_index,
                    'is_complete': False
                })
                current_chunk = [para]
                current_length = len(header) + para_len
                chunk_index += 1
            else:
                current_chunk.append(para)
                current_length += para_len
        
        # Save remaining chunk
        if current_chunk:
            chunk_text = header + '\n\n'.join(current_chunk)
            chunks.append({
                'text': chunk_text,
                'clause_number': clause.clause_number,
                'clause_title': clause.clause_title,
                'chunk_index': chunk_index,
                'is_complete': len(chunks) == 0
            })
        
        return chunks
```

#### Step 3: Modify ContractIngestor

In `ContractIngestor.__init__`, add:

```python
self.clause_extractor = ClauseExtractor()  # After self.chunker line
```

In `ContractIngestor.ingest_file`, replace:

```python
# OLD:
chunks = self.chunker.chunk_text(text)

# NEW:
clauses = self.clause_extractor.extract_clauses(text)
logger.info(f"Extracted {len(clauses)} clauses from {filename}")
```

Update the payload creation loop:

```python
# OLD loop over chunks
for idx, chunk in enumerate(chunks):
    # ...

# NEW loop over clauses
for clause in clauses:
    clause_chunks = self.clause_extractor.split_long_clause(
        clause, 
        max_length=self.config.CHUNK_SIZE
    )
    
    for chunk_data in clause_chunks:
        chunk_text = chunk_data['text']
        chunk_checksum = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        
        metadata = {
            "upload_id": upload_id,
            "document_id": None,
            "organization_id": str(organization_id),
            "project_id": str(project_id) if project_id else None,
            "uploadType": "contract",
            "letterNo": None,
            "source_file": str(file_path_obj),
            "source_filename": filename,
            
            # NEW CLAUSE METADATA FIELDS
            "clause_number": chunk_data['clause_number'],
            "clause_title": chunk_data['clause_title'],
            "clause_type": clause.clause_type,
            "clause_level": clause.level,
            "parent_clause_number": clause.parent_number,
            "chunk_index": chunk_data['chunk_index'],
            "is_complete_clause": chunk_data['is_complete'],
            "clause_start_position": clause.start_position,
            "clause_end_position": clause.end_position,
            
            "tags": final_tags,
            "checksum_sha256": chunk_checksum,
            "source": "contracts_ingest",
        }
        
        payloads.append({
            "text": chunk_text, 
            "metadata": metadata, 
            "checksum": chunk_checksum
        })
```

---

### Phase 2: Frontend - Clause Display

#### ContractsSearchPage.tsx Modifications

**1. Add TypeScript interfaces:**

```typescript
interface ClauseResult {
  clause_number: string;
  clause_title: string;
  clause_type: string;
  clause_level: number;
  is_complete_clause: boolean;
  parent_clause_number?: string;
  text: string;
  source_filename: string;
  score?: number;
  document_id?: string;
  chunk_index?: number;
}
```

**2. Add grouping helper:**

```typescript
const groupChunksByClause = (results: any[]): Map<string, ClauseResult[]> => {
  const clauseMap = new Map<string, ClauseResult[]>();
  
  results.forEach(result => {
    const key = `${result.source_filename}_${result.clause_number}`;
    
    if (!clauseMap.has(key)) {
      clauseMap.set(key, []);
    }
    
    clauseMap.get(key)!.push({
      clause_number: result.clause_number || 'N/A',
      clause_title: result.clause_title || 'Untitled Clause',
      clause_type: result.clause_type || 'clause',
      clause_level: result.clause_level || 1,
      is_complete_clause: result.is_complete_clause || false,
      parent_clause_number: result.parent_clause_number,
      text: result.text,
      source_filename: result.source_filename || 'Unknown',
      score: result.score,
      document_id: result.document_id,
      chunk_index: result.chunk_index || 0
    });
  });
  
  return clauseMap;
};
```

**3. Add ClauseResultCard component:**

```typescript
const ClauseResultCard: React.FC<{ clauseKey: string; chunks: ClauseResult[] }> = ({ 
  clauseKey, 
  chunks 
}) => {
  const [expanded, setExpanded] = useState(false);
  const firstChunk = chunks[0];
  const isMultiChunk = chunks.length > 1;
  
  const completeClauseText = chunks
    .sort((a, b) => (a.chunk_index || 0) - (b.chunk_index || 0))
    .map(c => c.text)
    .join('\n\n');
  
  const displayText = expanded ? completeClauseText : 
    (completeClauseText.length > 500 
      ? completeClauseText.slice(0, 500) + '...' 
      : completeClauseText);
  
  return (
    <Card className="mb-4 hover:shadow-lg transition-shadow">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <Badge variant="outline" className="font-mono text-xs">
                {firstChunk.clause_number}
              </Badge>
              <Badge variant="secondary" className="text-xs capitalize">
                {firstChunk.clause_type}
              </Badge>
              {isMultiChunk && (
                <Badge variant="default" className="text-xs">
                  {chunks.length} parts
                </Badge>
              )}
              {firstChunk.is_complete_clause && (
                <Badge variant="success" className="text-xs">
                  Complete
                </Badge>
              )}
            </div>
            <CardTitle className="text-lg font-semibold">
              {firstChunk.clause_title}
            </CardTitle>
            <CardDescription className="text-sm mt-1">
              {firstChunk.source_filename}
              {firstChunk.parent_clause_number && (
                <span className="ml-2 text-muted-foreground">
                  • Parent: {firstChunk.parent_clause_number}
                </span>
              )}
            </CardDescription>
          </div>
          {firstChunk.score && (
            <div className="text-right ml-4">
              <div className="text-xs text-muted-foreground">Relevance</div>
              <div className="text-sm font-semibold">
                {(firstChunk.score * 100).toFixed(1)}%
              </div>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="prose prose-sm max-w-none">
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed bg-muted/30 p-4 rounded-lg">
            {displayText}
          </pre>
        </div>
        {completeClauseText.length > 500 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded(!expanded)}
            className="mt-3"
          >
            {expanded ? (
              <>
                <ChevronUp className="h-4 w-4 mr-1" />
                Show Less
              </>
            ) : (
              <>
                <ChevronDown className="h-4 w-4 mr-1" />
                Show Complete Clause
              </>
            )}
          </Button>
        )}
      </CardContent>
    </Card>
  );
};
```

**4. Replace results rendering:**

```typescript
{searchResult && searchResult.results && (() => {
  const clauseMap = groupChunksByClause(searchResult.results);
  
  return (
    <div className="mt-6">
      <h3 className="text-lg font-semibold mb-4">
        Found {clauseMap.size} Relevant Clauses
      </h3>
      {Array.from(clauseMap.entries()).map(([key, chunks]) => (
        <ClauseResultCard key={key} clauseKey={key} chunks={chunks} />
      ))}
    </div>
  );
})()}
```

---

## Key Benefits

✅ **Complete Clauses** - Users see entire clause content, not fragments  
✅ **Proper Headers** - Clear clause identification (e.g., "CLAUSE 4.2: Payment Terms")  
✅ **Intelligent Splitting** - Long clauses split at natural boundaries (paragraphs/sentences)  
✅ **Chunk Reassembly** - Frontend automatically combines multi-part clauses  
✅ **Rich Metadata** - Clause number, title, type, hierarchy level, parent relationship  
✅ **Professional UX** - Clean, readable presentation with expand/collapse  
✅ **Backward Compatible** - Documents without clauses treated as single unit  

---

## Testing Checklist

- [ ] Upload contract with numbered clauses (1, 1.1, 1.2, 2, 2.1, etc.)
- [ ] Check backend logs show "Extracted X clauses from filename"
- [ ] Verify database has new clause metadata fields
- [ ] Search for clause-related topic
- [ ] Confirm frontend shows complete clause with header
- [ ] Verify multi-chunk clauses are combined
- [ ] Test expand/collapse for long clauses
- [ ] Check clause hierarchy badges display correctly

---

## Database Schema Updates

No migration needed - new fields added to existing `document_vectors` collection:

```javascript
{
  // ... existing fields ...
  clause_number: "1.2.3",
  clause_title: "Payment Terms",
  clause_type: "clause",
  clause_level: 3,
  parent_clause_number: "1.2",
  chunk_index: 0,
  is_complete_clause: true,
  clause_start_position: 1250,
  clause_end_position: 3780
}
```

---

## Expected Results

**Before:**
```
Search Results:
- [Chunk 1] "...contractor shall pay within 30 days of invoice. The payment..."
- [Chunk 2] "...terms are subject to approval. Late payment penalties apply..."
```

**After:**
```
Search Results:
- CLAUSE 4.2: Payment Terms
  The Contractor shall pay the Employer within 30 days of receiving 
  a valid invoice. The payment terms are subject to approval by the 
  Engineer. Late payment penalties apply at 2% per month. All 
  payments shall be made in USD to the account specified...
  
  [Show Complete Clause]
```

---

## Support for Various Clause Formats

The ClauseExtractor supports:
- `CLAUSE 1.2.3 - Payment Terms`
- `Section 5: Termination`
- `Article 12 General Provisions`
- `4.2.3 Insurance Requirements`
- `1.2.3. Scope of Work`

Adjust regex patterns in `CLAUSE_PATTERNS` for document-specific formats.
