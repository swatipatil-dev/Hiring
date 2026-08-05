import { useEffect, useRef, useState } from 'react'

// Encapsulates the candidate-photo capture flow: live webcam preview, snapshot
// to JPEG file, or fallback file upload. Cleans up the MediaStream on unmount
// so we never leak the user's camera after they navigate away.
export default function useCameraStream() {
  const [photo, setPhoto] = useState(null)              // File or Blob
  const [photoPreview, setPhotoPreview] = useState(null) // data URL
  const [photoSource, setPhotoSource] = useState(null)   // 'camera' | 'upload'
  const [cameraOpen, setCameraOpen] = useState(false)
  const [cameraError, setCameraError] = useState('')

  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const fileInputRef = useRef(null)

  const stopStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
  }

  const closeCamera = () => {
    stopStream()
    setCameraOpen(false)
  }

  const openCamera = async () => {
    setCameraError('')
    setCameraOpen(true)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 480, height: 360, facingMode: 'user' },
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        videoRef.current.play()
      }
    } catch {
      setCameraOpen(false)
      setCameraError('Camera access denied. Please allow camera or upload a photo instead.')
    }
  }

  const capturePhoto = () => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d').drawImage(video, 0, 0)
    canvas.toBlob(
      (blob) => {
        if (blob) {
          setPhoto(new File([blob], 'photo.jpg', { type: 'image/jpeg' }))
          setPhotoPreview(canvas.toDataURL('image/jpeg'))
          setPhotoSource('camera')
        }
      },
      'image/jpeg',
      0.9,
    )
    closeCamera()
  }

  const handlePhotoFile = (e) => {
    const file = e.target.files[0]
    if (!file) return
    setPhoto(file)
    setPhotoSource('upload')
    const reader = new FileReader()
    reader.onload = () => setPhotoPreview(reader.result)
    reader.readAsDataURL(file)
  }

  const clearPhoto = () => {
    setPhoto(null)
    setPhotoPreview(null)
    setPhotoSource(null)
    setCameraError('')
  }

  const reset = () => {
    closeCamera()
    clearPhoto()
  }

  // Guarantee the camera stream is released even if the component unmounts
  // while the camera is open (route change, modal teardown, etc).
  useEffect(() => stopStream, [])

  return {
    photo,
    photoPreview,
    photoSource,
    cameraOpen,
    cameraError,
    videoRef,
    canvasRef,
    fileInputRef,
    openFilePicker: () => fileInputRef.current?.click(),
    openCamera,
    closeCamera,
    capturePhoto,
    handlePhotoFile,
    clearPhoto,
    reset,
  }
}
