// Enhanced UploadPage.tsx with Bulk Upload Support
import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useDropzone } from 'react-dropzone';
import Papa from 'papaparse';
import { toast } from 'react-hot-toast';
import { 
  Upload, 
  FileText, 
  X, 
  Download, 
  AlertCircle, 
  CheckCircle,
  Clock,
  FileSpreadsheet,
  FolderOpen,
  Settings
} from 'lucide-react';

interface FileWithMetadata {
  file: File;
  metadata?: DocumentMetadata;
  status: 'pending' | 'processing' | 'completed' | 'error';
  error?: string;
  documentId?: string;
}

interface DocumentMetadata {
  filename: string;
  upload_type: 'incoming' | 'outgoing';
  letter_no: string;
  date: string;
  subject: string;
  from?: string;
  to?: string;
  tags?: string[];
  sub_tags?: string[];
  status?: string;
  ocr_enabled?: boolean;
}

interface BulkUploadStatus {
  job_id: string;
  total_files: number;
  processed_files: number;
  successful_uploads: number;
  failed_uploads: number;
  status: 'processing' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled';
  progress_percentage: number;
  estimated_completion?: string;
  processing_rate?: number;
  results: DocumentProcessingResult[];
}

interface DocumentProcessingResult {
  filename: string;
  success: boolean;
  document_id?: string;
  error?: string;
  row_number: number;
  processing_time?: number;
  metadata_extracted: boolean;
  ocr_completed: boolean;
  embeddings_created: number;
}

const UploadPage: React.FC = () => {
  const [files, setFiles] = useState<FileWithMetadata[]>([]);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvData, setCsvData] = useState<DocumentMetadata[]>([]);
  const [uploadType, setUploadType] = useState<'single' | 'bulk' | 'folder'>('single');
  const [pathStructure, setPathStructure] = useState('');
  const [organizationId, setOrganizationId] = useState('');
  const [projectId, setProjectId] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [bulkUploadStatus, setBulkUploadStatus] = useState<BulkUploadStatus | null>(null);
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [compressionEnabled, setCompressionEnabled] = useState(false);
  
  const csvInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  // Dropzone for document files
  const onDrop = useCallback((acceptedFiles: File[]) => {
    const newFiles: FileWithMetadata[] = acceptedFiles.map(file => ({
      file,
      status: 'pending'
    }));
    
    setFiles(prev => [...prev, ...newFiles]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/msword': ['.doc'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'text/plain': ['.txt'],
      'image/jpeg': ['.jpg', '.jpeg'],
      'image/png': ['.png'],
      'image/gif': ['.gif']
    },
    multiple: true
  });

  // Handle CSV file selection
  const handleCsvUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && file.type === 'text/csv') {
      setCsvFile(file);
      parseCsvFile(file);
    } else {
      toast.error('Please select a valid CSV file');
    }
  };

  // Parse CSV file
  const parseCsvFile = (file: File) => {
    Papa.parse<DocumentMetadata>(file, {
      header: true,
      skipEmptyLines: true,
      complete: (results) => {
        if (results.errors.length > 0) {
          toast.error('CSV parsing errors detected. Please check the file format.');
          console.error('CSV parsing errors:', results.errors);
          return;
        }

        // Validate CSV data
        const validatedData = validateCsvData(results.data);
        setCsvData(validatedData);
        
        if (validatedData.length === 0) {
          toast.error('No valid rows found in CSV file');
        } else {
          toast.success(`Successfully parsed ${validatedData.length} rows from CSV`);
        }
      },
      error: (error) => {
        toast.error('Failed to parse CSV file');
        console.error('CSV parsing error:', error);
      }
    });
  };

  // Validate CSV data
  const validateCsvData = (data: any[]): DocumentMetadata[] => {
    const requiredFields = ['filename', 'upload_type', 'letter_no', 'date', 'subject'];
    const validRows: DocumentMetadata[] = [];

    data.forEach((row, index) => {
      const missingFields = requiredFields.filter(field => !row[field] || row[field].trim() === '');
      
      if (missingFields.length === 0) {
        // Validate upload_type
        if (!['incoming', 'outgoing'].includes(row.upload_type?.toLowerCase())) {
          console.warn(`Row ${index + 1}: Invalid upload_type '${row.upload_type}'`);
          return;
        }

        // Validate date format
        const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
        if (!dateRegex.test(row.date)) {
          console.warn(`Row ${index + 1}: Invalid date format '${row.date}' (expected YYYY-MM-DD)`);
          return;
        }

        validRows.push({
          filename: row.filename.trim(),
          upload_type: row.upload_type.toLowerCase() as 'incoming' | 'outgoing',
          letter_no: row.letter_no.trim(),
          date: row.date.trim(),
          subject: row.subject.trim(),
          from: row.from?.trim() || undefined,
          to: row.to?.trim() || undefined,
          tags: row.tags ? row.tags.split(',').map((t: string) => t.trim()) : undefined,
          sub_tags: row.sub_tags ? row.sub_tags.split(',').map((t: string) => t.trim()) : undefined,
          status: row.status?.trim() || 'draft',
          ocr_enabled: row.ocr_enabled?.toLowerCase() === 'true'
        });
      } else {
        console.warn(`Row ${index + 1}: Missing required fields: ${missingFields.join(', ')}`);
      }
    });

    return validRows;
  };

  // Handle folder upload
  const handleFolderUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    onDrop(files);
    toast.success(`Added ${files.length} files from folder`);
  };

  // Remove file from list
  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  // Download CSV template
  const downloadCsvTemplate = () => {
    const template = [
      'filename,upload_type,letter_no,date,subject,from,to,tags,sub_tags,status,ocr_enabled',
      'sample-letter-001.pdf,incoming,LTR-2024-001,2024-01-15,Contract Amendment Request,Contractor Corp,Project Manager,"legal,contract","high-priority,urgent",draft,true',
      'sample-letter-002.pdf,outgoing,LTR-2024-002,2024-01-16,Response to Amendment Request,Project Manager,Contractor Corp,"legal,response","normal",review,false',
      'sample-invoice-001.pdf,incoming,INV-2024-001,2024-01-17,Monthly Progress Invoice,Supplier Ltd,Accounts Payable,"invoice,payment","finance,monthly",approved,true'
    ].join('\n');

    const blob = new Blob([template], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bulk-upload-template.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    toast.success('CSV template downloaded successfully');
  };

  // Handle single file upload
  const handleSingleUpload = async () => {
    if (files.length === 0) {
      toast.error('Please select at least one file');
      return;
    }

    setIsUploading(true);
    
    try {
      for (const fileWithMetadata of files) {
        const formData = new FormData();
        formData.append('file', fileWithMetadata.file);
        formData.append('organization_id', organizationId);
        formData.append('project_id', projectId);
        formData.append('uploadType', 'incoming'); // Default for single upload
        formData.append('letterNo', `AUTO-${Date.now()}`); // Auto-generate
        formData.append('date', new Date().toISOString().split('T')[0]);
        formData.append('subject', fileWithMetadata.file.name);
        formData.append('ocrEnabled', ocrEnabled ? '1' : '0');

        const response = await fetch('/api/documents', {
          method: 'POST',
          body: formData,
        });

        if (!response.ok) {
          throw new Error(`Upload failed: ${response.statusText}`);
        }

        const result = await response.json();
        
        // Update file status
        setFiles(prev => prev.map(f => 
          f === fileWithMetadata 
            ? { ...f, status: 'completed', documentId: result.id }
            : f
        ));
      }
      
      toast.success('All files uploaded successfully');
    } catch (error) {
      toast.error(`Upload failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsUploading(false);
    }
  };

  // Handle bulk upload
  const handleBulkUpload = async () => {
    if (files.length === 0 || !csvFile || csvData.length === 0) {
      toast.error('Please select both CSV file and document files for bulk upload');
      return;
    }

    // Validate that all CSV filenames have corresponding files
    const fileNames = files.map(f => f.file.name);
    const csvFilenames = csvData.map(d => d.filename);
    const missingFiles = csvFilenames.filter(name => !fileNames.includes(name));
    const extraFiles = fileNames.filter(name => !csvFilenames.includes(name));

    if (missingFiles.length > 0) {
      toast.error(`Missing files referenced in CSV: ${missingFiles.join(', ')}`);
      return;
    }

    if (extraFiles.length > 0) {
      toast.warning(`Extra files not in CSV will be ignored: ${extraFiles.join(', ')}`);
    }

    setIsUploading(true);
    
    try {
      const formData = new FormData();
      
      // Add CSV file
      formData.append('csv_file', csvFile);
      
      // Add document files
      files.forEach(fileWithMetadata => {
        if (csvFilenames.includes(fileWithMetadata.file.name)) {
          formData.append('files', fileWithMetadata.file);
        }
      });
      
      // Add organization and project info
      formData.append('organization_id', organizationId);
      formData.append('project_id', projectId);

      const response = await fetch('/api/documents/bulk-upload', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`Bulk upload failed: ${response.statusText}`);
      }

      const result = await response.json();
      
      // Start polling for status
      setBulkUploadStatus({
        job_id: result.job_id,
        total_files: result.total_files,
        processed_files: 0,
        successful_uploads: 0,
        failed_uploads: 0,
        status: 'processing',
        progress_percentage: 0,
        results: []
      });
      
      pollBulkUploadStatus(result.job_id);
      
      toast.success(`Bulk upload started. Job ID: ${result.job_id}`);
    } catch (error) {
      toast.error(`Bulk upload failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsUploading(false);
    }
  };

  // Poll bulk upload status
  const pollBulkUploadStatus = async (jobId: string) => {
    const pollInterval = setInterval(async () => {
      try {
        const response = await fetch(`/api/documents/bulk-upload/${jobId}/status`);
        if (!response.ok) {
          throw new Error('Failed to fetch status');
        }

        const status: BulkUploadStatus = await response.json();
        setBulkUploadStatus(status);

        if (['completed', 'completed_with_errors', 'failed', 'cancelled'].includes(status.status)) {
          clearInterval(pollInterval);
          
          if (status.status === 'completed') {
            toast.success(`Bulk upload completed successfully! ${status.successful_uploads} files uploaded.`);
          } else if (status.status === 'completed_with_errors') {
            toast.warning(`Bulk upload completed with errors. ${status.successful_uploads} successful, ${status.failed_uploads} failed.`);
          } else {
            toast.error(`Bulk upload ${status.status}`);
          }
        }
      } catch (error) {
        console.error('Failed to poll status:', error);
        clearInterval(pollInterval);
      }
    }, 2000); // Poll every 2 seconds
  };

  // Render upload progress
  const renderUploadProgress = () => {
    if (!bulkUploadStatus) return null;

    const { status, progress_percentage, processed_files, total_files, successful_uploads, failed_uploads } = bulkUploadStatus;

    return (
      <div className="bg-white border rounded-lg p-6 mt-6">
        <h3 className="text-lg font-semibold mb-4">Bulk Upload Progress</h3>
        
        {/* Progress bar */}
        <div className="w-full bg-gray-200 rounded-full h-2 mb-4">
          <div 
            className="bg-blue-600 h-2 rounded-full transition-all duration-300"
            style={{ width: `${progress_percentage}%` }}
          />
        </div>

        {/* Status info */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <div className="text-center">
            <div className="text-2xl font-bold text-gray-700">{processed_files}</div>
            <div className="text-sm text-gray-500">Processed</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-green-600">{successful_uploads}</div>
            <div className="text-sm text-gray-500">Successful</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-red-600">{failed_uploads}</div>
            <div className="text-sm text-gray-500">Failed</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-blue-600">{total_files}</div>
            <div className="text-sm text-gray-500">Total</div>
          </div>
        </div>

        {/* Status badge */}
        <div className="flex items-center justify-center">
          <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${
            status === 'processing' ? 'bg-blue-100 text-blue-800' :
            status === 'completed' ? 'bg-green-100 text-green-800' :
            status === 'completed_with_errors' ? 'bg-yellow-100 text-yellow-800' :
            'bg-red-100 text-red-800'
          }`}>
            {status === 'processing' && <Clock className="w-4 h-4 mr-1" />}
            {status === 'completed' && <CheckCircle className="w-4 h-4 mr-1" />}
            {(status === 'completed_with_errors' || status === 'failed') && <AlertCircle className="w-4 h-4 mr-1" />}
            {status.replace('_', ' ').toUpperCase()}
          </span>
        </div>

        {/* Processing results */}
        {bulkUploadStatus.results.length > 0 && (
          <div className="mt-6">
            <h4 className="font-medium mb-3">Processing Results</h4>
            <div className="max-h-64 overflow-y-auto space-y-2">
              {bulkUploadStatus.results.map((result, index) => (
                <div key={index} className={`flex items-center justify-between p-2 rounded border ${
                  result.success ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'
                }`}>
                  <div className="flex items-center">
                    {result.success ? 
                      <CheckCircle className="w-4 h-4 text-green-500 mr-2" /> :
                      <AlertCircle className="w-4 h-4 text-red-500 mr-2" />
                    }
                    <span className="text-sm font-medium">{result.filename}</span>
                  </div>
                  <div className="text-xs text-gray-500">
                    {result.success ? 
                      `✓ ${result.ocr_completed ? 'OCR' : ''} ${result.embeddings_created > 0 ? `${result.embeddings_created} chunks` : ''}` :
                      result.error
                    }
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="bg-white rounded-lg shadow-sm border">
        <div className="border-b p-6">
          <h1 className="text-2xl font-bold text-gray-900">Upload Documents</h1>
          <p className="text-gray-600 mt-2">
            Upload single files, bulk upload with CSV metadata, or upload entire folders
          </p>
        </div>

        <div className="p-6">
          {/* Upload Type Selection */}
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-3">
              Upload Type
            </label>
            <div className="flex space-x-4">
              <button
                onClick={() => setUploadType('single')}
                className={`flex items-center px-4 py-2 rounded-lg border ${
                  uploadType === 'single' 
                    ? 'border-blue-500 bg-blue-50 text-blue-700' 
                    : 'border-gray-300 hover:border-gray-400'
                }`}
              >
                <FileText className="w-4 h-4 mr-2" />
                Single Files
              </button>
              <button
                onClick={() => setUploadType('bulk')}
                className={`flex items-center px-4 py-2 rounded-lg border ${
                  uploadType === 'bulk' 
                    ? 'border-blue-500 bg-blue-50 text-blue-700' 
                    : 'border-gray-300 hover:border-gray-400'
                }`}
              >
                <FileSpreadsheet className="w-4 h-4 mr-2" />
                Bulk Upload (CSV)
              </button>
              <button
                onClick={() => setUploadType('folder')}
                className={`flex items-center px-4 py-2 rounded-lg border ${
                  uploadType === 'folder' 
                    ? 'border-blue-500 bg-blue-50 text-blue-700' 
                    : 'border-gray-300 hover:border-gray-400'
                }`}
              >
                <FolderOpen className="w-4 h-4 mr-2" />
                Folder Upload
              </button>
            </div>
          </div>

          {/* Organization and Project Info */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Organization ID *
              </label>
              <input
                type="text"
                value={organizationId}
                onChange={(e) => setOrganizationId(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Enter organization ID"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Project ID *
              </label>
              <input
                type="text"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Enter project ID"
                required
              />
            </div>
          </div>

          {/* Bulk Upload CSV Section */}
          {uploadType === 'bulk' && (
            <div className="mb-6 p-4 border rounded-lg bg-blue-50">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium text-gray-900">CSV Metadata File</h3>
                <button
                  onClick={downloadCsvTemplate}
                  className="flex items-center px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
                >
                  <Download className="w-4 h-4 mr-1" />
                  Download Template
                </button>
              </div>
              
              <input
                ref={csvInputRef}
                type="file"
                accept=".csv"
                onChange={handleCsvUpload}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg"
              />
              
              {csvFile && (
                <div className="mt-2 text-sm text-green-600">
                  ✓ CSV file loaded: {csvFile.name} ({csvData.length} valid rows)
                </div>
              )}
            </div>
          )}

          {/* Folder Upload Section */}
          {uploadType === 'folder' && (
            <div className="mb-6">
              <input
                ref={folderInputRef}
                type="file"
                webkitdirectory=""
                multiple
                onChange={handleFolderUpload}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg"
              />
            </div>
          )}

          {/* File Drop Zone */}
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              isDragActive 
                ? 'border-blue-500 bg-blue-50' 
                : 'border-gray-300 hover:border-gray-400'
            }`}
          >
            <input {...getInputProps()} />
            <Upload className="w-12 h-12 text-gray-400 mx-auto mb-4" />
            
            {isDragActive ? (
              <p className="text-blue-600">Drop the files here...</p>
            ) : (
              <div>
                <p className="text-gray-600 mb-2">
                  Drag & drop files here, or click to select files
                </p>
                <p className="text-sm text-gray-500">
                  {uploadType === 'incoming' 
                    ? "Support for PDF, DOC, DOCX, TXT, JPG, PNG, GIF images files" 
                    : "Support for PDF, DOC, DOCX, TXT, JPG, PNG, GIF images files"}
                </p>
              </div>
            )}
          </div>

          {/* Selected Files List */}
          {files.length > 0 && (
            <div className="mt-6">
              <h3 className="font-medium text-gray-900 mb-3">
                Selected Files ({files.length} file{files.length !== 1 ? 's' : ''})
              </h3>
              
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {files.map((fileWithMetadata, index) => (
                  <div key={index} className="flex items-center justify-between p-3 border rounded-lg">
                    <div className="flex items-center">
                      <FileText className="w-5 h-5 text-gray-400 mr-3" />
                      <div>
                        <div className="font-medium">{fileWithMetadata.file.name}</div>
                        <div className="text-sm text-gray-500">
                          {(fileWithMetadata.file.size / 1024 / 1024).toFixed(2)} MB
                        </div>
                      </div>
                    </div>
                    
                    <div className="flex items-center space-x-2">
                      {/* Status indicator */}
                      {fileWithMetadata.status === 'completed' && <CheckCircle className="w-5 h-5 text-green-500" />}
                      {fileWithMetadata.status === 'processing' && <Clock className="w-5 h-5 text-blue-500" />}
                      {fileWithMetadata.status === 'error' && <AlertCircle className="w-5 h-5 text-red-500" />}
                      
                      {/* Remove button */}
                      <button
                        onClick={() => removeFile(index)}
                        className="text-red-500 hover:text-red-700"
                      >
                        <X className="w-5 h-5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              {pathStructure && (
                <div className="mt-3 text-sm text-gray-600">
                  Files will be saved to: <span className="font-mono bg-gray-100 px-2 py-1 rounded">
                    {pathStructure}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Advanced Settings */}
          <div className="mt-6">
            <button
              onClick={() => setShowAdvancedSettings(!showAdvancedSettings)}
              className="flex items-center text-sm text-gray-600 hover:text-gray-800"
            >
              <Settings className="w-4 h-4 mr-1" />
              Advanced Settings
              <span className="ml-1">{showAdvancedSettings ? '▼' : '▶'}</span>
            </button>

            {showAdvancedSettings && (
              <div className="mt-3 p-4 border rounded-lg bg-gray-50 space-y-4">
                <div className="flex items-center space-x-4">
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={ocrEnabled}
                      onChange={(e) => setOcrEnabled(e.target.checked)}
                      className="mr-2"
                    />
                    <span className="text-sm">Extract text and save project summary</span>
                  </label>
                </div>
                
                <div className="flex items-center space-x-4">
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={compressionEnabled}
                      onChange={(e) => setCompressionEnabled(e.target.checked)}
                      className="mr-2"
                    />
                    <span className="text-sm">Reduce file size for storage</span>
                  </label>
                </div>
              </div>
            )}
          </div>

          {/* Upload Button */}
          <div className="mt-6 flex justify-end">
            {uploadType === 'bulk' ? (
              <button
                onClick={handleBulkUpload}
                disabled={isUploading || files.length === 0 || !csvFile || !organizationId || !projectId}
                className="flex items-center px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                {isUploading ? (
                  <>
                    <Clock className="w-4 h-4 mr-2 animate-spin" />
                    Starting Bulk Upload...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4 mr-2" />
                    Start Bulk Upload
                  </>
                )}
              </button>
            ) : (
              <button
                onClick={handleSingleUpload}
                disabled={isUploading || files.length === 0 || !organizationId || !projectId}
                className="flex items-center px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                {isUploading ? (
                  <>
                    <Clock className="w-4 h-4 mr-2 animate-spin" />
                    Uploading...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4 mr-2" />
                    Upload Files
                  </>
                )}
              </button>
            )}
          </div>

          {/* Upload Progress */}
          {renderUploadProgress()}
        </div>
      </div>
    </div>
  );
};

export default UploadPage;